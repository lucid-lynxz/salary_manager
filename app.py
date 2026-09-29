import os
import re
import json
import uuid
import sqlite3
import datetime
import platform
import subprocess
import zipfile
from pathlib import Path
from functools import wraps

import pandas as pd
from dateutil import parser as date_parser
from flask import Flask, request, jsonify, render_template, send_from_directory

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "salary.db"
USER_CONFIG_PATH = BASE_DIR / "user_config.json"
UPLOAD_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS salary_imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER NOT NULL,
        month INTEGER NOT NULL,
        salary_filename TEXT,
        email_filename TEXT,
        imported_at TEXT NOT NULL,
        UNIQUE(year, month)
    );

    CREATE TABLE IF NOT EXISTS persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        import_id INTEGER NOT NULL REFERENCES salary_imports(id) ON DELETE CASCADE,
        community TEXT NOT NULL,
        name TEXT NOT NULL,
        email TEXT,
        email_status TEXT NOT NULL DEFAULT 'unknown',
        email_issues TEXT,
        UNIQUE(import_id, community, name)
    );

    CREATE TABLE IF NOT EXISTS salary_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        import_id INTEGER NOT NULL REFERENCES salary_imports(id) ON DELETE CASCADE,
        person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
        data_json TEXT NOT NULL,
        gross_salary REAL,
        total_deduction REAL,
        net_salary REAL,
        bank_account TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_persons_name ON persons(name);
    CREATE INDEX IF NOT EXISTS idx_persons_community ON persons(community);
    CREATE INDEX IF NOT EXISTS idx_persons_import ON persons(import_id);
    CREATE INDEX IF NOT EXISTS idx_salary_import ON salary_records(import_id);
    """
    with get_db() as conn:
        conn.executescript(schema)


init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)


def normalize_text(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_header(value):
    """列名归一化：删除所有空白字符（空格、换行、制表符等）。"""
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", "", str(value))


def clean_email(raw):
    if pd.isna(raw):
        return ""
    text = str(raw).strip()
    text = re.sub(r"[\s\n\r\t]+", "", text)
    text = text.replace("＠", "@")
    text = text.replace(" at ", "@")
    return text.lower()


def validate_email(raw_email, seen_emails=None):
    seen_emails = seen_emails or set()
    issues = []
    email = clean_email(raw_email)
    if not email:
        issues.append("邮箱为空")
        return email, "invalid", issues
    if "@" not in email:
        issues.append("缺少 @ 符号")
    if EMAIL_RE.match(email) is None:
        issues.append("邮箱格式无效")
    if email in seen_emails:
        issues.append("邮箱重复")
    if issues:
        return email, "invalid", issues
    return email, "valid", []


def save_upload(file_storage):
    ext = Path(file_storage.filename).suffix.lower()
    file_id = f"{uuid.uuid4().hex}{ext}"
    path = UPLOAD_DIR / file_id
    file_storage.save(path)
    return file_id, str(path)


def read_excel_raw(path, sheet_name):
    return pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)


def detect_header_row(df, required_keywords=None):
    required_keywords = required_keywords or ["姓名", "社区"]
    for idx, row in df.iterrows():
        values = [normalize_text(v) for v in row.tolist()]
        joined = "".join(values)
        if all(kw in joined for kw in required_keywords):
            return int(idx)
    return None


def merge_headers(df, header_rows):
    """合并多行表头，返回列名列表（列名中的空白字符会被删除）。"""
    if not header_rows:
        return [f"列{i}" for i in range(df.shape[1])]
    rows = [df.iloc[r].tolist() for r in header_rows]
    headers = []
    for col_idx in range(df.shape[1]):
        parts = []
        for r in rows:
            val = normalize_header(r[col_idx])
            if val and val not in parts:
                parts.append(val)
        headers.append("".join(parts) if parts else f"列{col_idx}")
    return headers


def fill_merged_headers(df, header_rows):
    """合并多行表头。
    规则：对每一列，从上到下查看配置的表头行；哪一行有值就用哪一行作为列名，
    若多行都有值，则以行号更大（更靠下）的那一行为准。
    同时仍对第一行做横向填充，用于处理合并单元格的父标题场景。
    """
    if not header_rows:
        return [f"列{i}" for i in range(df.shape[1])]
    rows = [df.iloc[r].tolist() for r in header_rows]

    # 第一行横向填充，识别合并单元格的父标题
    top_row = rows[0]
    last = ""
    filled_top = []
    for val in top_row:
        val = normalize_header(val)
        if val:
            last = val
        filled_top.append(last)

    headers = []
    seen_counts = {}
    for col_idx in range(df.shape[1]):
        # 从下到上找第一个非空单元格，即行号最大者
        bottom_name = ""
        for r in reversed(rows):
            val = normalize_header(r[col_idx])
            if val:
                bottom_name = val
                break
        base = bottom_name or filled_top[col_idx] or f"列{col_idx}"
        # 去重：同名列加序号后缀
        seen_counts[base] = seen_counts.get(base, 0) + 1
        if seen_counts[base] > 1:
            headers.append(f"{base}({seen_counts[base]})")
        else:
            headers.append(base)
    return headers


def suggest_column_mapping(headers, candidates):
    """
    candidates: {"name": ["姓名", ...], "community": [...], ...}
    返回 {key: header_index}。
    匹配时忽略列名与关键词中的空白字符。
    """
    mapping = {}
    clean_headers = [re.sub(r"\s+", "", h) for h in headers]
    for key, keywords in candidates.items():
        for idx, h in enumerate(clean_headers):
            if any(re.sub(r"\s+", "", kw) in h for kw in keywords):
                mapping[key] = idx
                break
    return mapping


def extract_salary_date(df):
    """从表格前 5 行中尝试提取工资月份。"""
    for idx in range(min(5, len(df))):
        row_text = " ".join(str(v) for v in df.iloc[idx].tolist() if pd.notna(v))
        # 2025年1月 / 2025-01 / 2025/01 / 2025.01
        m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", row_text)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r"(\d{4})[-/.](\d{1,2})", row_text)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


def extract_date_from_filename(filename):
    """从文件名中尝试提取年月，如 2025.01、2025-01、2025/01、2025年1月。"""
    if not filename:
        return None, None
    # 2025.01 / 2025-01 / 2025/01
    m = re.search(r"(\d{4})[-/.](\d{1,2})", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 2025年1月
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def col_letters_to_index(letters):
    """Excel 列字母转 0-based 索引，如 A->0, Z->25, AA->26。空值返回 None。"""
    if not letters:
        return None
    letters = str(letters).strip().upper()
    if letters.isdigit():
        return int(letters) - 1
    n = 0
    for ch in letters:
        if not ('A' <= ch <= 'Z'):
            return None
        n = n * 26 + (ord(ch) - ord('A') + 1)
    return n - 1


def apply_column_limit(df, data_end_col):
    """按 data_end_col 限制列数，返回切片后的 DataFrame。"""
    if not data_end_col:
        return df
    idx = col_letters_to_index(data_end_col)
    if idx is None or idx < 0:
        return df
    return df.iloc[:, : min(idx + 1, df.shape[1])]


def to_float(value):
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(text) if text else None
    except ValueError:
        return None


def simplify_date_value(value):
    """把类似 2005-03-01 00:00:00 的字符串简化为日期部分。"""
    if value is None:
        return ""
    text = str(value).strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}$", text)
    if m:
        return m.group(1)
    return text


def extract_person_summary(detail):
    """从历史工资明细的原始列中，尝试提取岗位、调资居龄、岗位工资等级年限起算时间等字段。"""
    if not detail:
        return {}
    result = {}
    items = list(detail.items())

    # 岗位工资等级年限起算时间（精确长标题优先）
    for header, value in items:
        h = re.sub(r"\s+", "", header)
        if "岗位工资等级年限起算时间" in h:
            result["position_start_date"] = simplify_date_value(value)
            break
    if "position_start_date" not in result:
        for header, value in items:
            h = re.sub(r"\s+", "", header)
            if "岗位工资等级" in h or "起算时间" in h:
                result["position_start_date"] = simplify_date_value(value)
                break

    # 调资居龄
    for header, value in items:
        h = re.sub(r"\s+", "", header)
        if "调资居龄" in h or "居龄" in h:
            result["service_years"] = value
            break

    # 岗位：匹配含"岗位"但不含"工资/等级/年限/起算"的短标题，优先精确匹配
    for header, value in items:
        h = re.sub(r"\s+", "", header)
        if h == "岗位":
            result["position"] = value
            break
    if "position" not in result:
        for header, value in items:
            h = re.sub(r"\s+", "", header)
            if "岗位" in h and not any(k in h for k in ["工资", "等级", "年限", "起算"]):
                result["position"] = value
                break

    return result


def parse_records(df, header_rows, data_start_row, data_end_row, mapping, skip_rows=None, is_email=False):
    headers = fill_merged_headers(df, header_rows)
    start = data_start_row
    end = data_end_row if data_end_row else len(df)
    skip_set = set(int(r) for r in (skip_rows or []))
    records = []
    for idx in range(start, end):
        excel_row = int(idx) + 1
        if excel_row in skip_set:
            continue
        row = df.iloc[idx].tolist()
        first_cell = normalize_text(row[0]) if row else ""
        if not first_cell or "计" in first_cell or "合计" in first_cell or "审核" in first_cell:
            continue
        record = {"_row_index": excel_row, "_raw": {h: normalize_text(v) for h, v in zip(headers, row)}}
        for key, col_idx in mapping.items():
            record[key] = normalize_text(row[col_idx]) if col_idx < len(row) else ""
        records.append(record)
    return records, headers


def clean_account(raw):
    if pd.isna(raw):
        return ""
    text = str(raw).strip()
    text = re.sub(r"[\s\n\r\t]+", "", text)
    text = re.sub(r"[^\d]", "", text)
    return text


def detect_salary_fields(headers):
    patterns = {
        "gross_salary": ["应发工资"],
        "total_deduction": ["代扣合计", "扣款合计"],
        "net_salary": ["实发工资"],
        "bank_account": ["领款人帐号", "领款人账号", "银行账号", "账号", "卡号"],
    }
    def norm(h):
        return re.sub(r"\s+", "", h)
    indexes = {}
    for field, pats in patterns.items():
        for idx, h in enumerate(headers):
            hn = norm(h)
            if any(re.sub(r"\s+", "", p) in hn for p in pats):
                indexes[field] = idx
                break
    return indexes


def get_previous_accounts():
    """返回上一次导入的 {(community, name): bank_account}。"""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM salary_imports ORDER BY year DESC, month DESC, id DESC LIMIT 1").fetchone()
        if not row:
            return {}
        prev_id = row["id"]
        rows = conn.execute("""
            SELECT p.community, p.name, s.bank_account
            FROM persons p
            JOIN salary_records s ON s.person_id = p.id
            WHERE p.import_id = ?
        """, (prev_id,)).fetchall()
    return {(r["community"], r["name"]): (r["bank_account"] or "") for r in rows}


def validate_salary_records(records, headers, field_indexes_override=None):
    """对工资记录做校验，返回 validation dict。
    field_indexes_override: 用户手动指定的列索引，若提供则优先使用，否则自动检测。
    """
    field_indexes = {}
    if field_indexes_override:
        for k, v in field_indexes_override.items():
            if v is not None and v != "":
                try:
                    field_indexes[k] = int(v)
                except (TypeError, ValueError):
                    pass
    # 若用户未提供有效列索引，则回退到自动检测
    if not field_indexes:
        field_indexes = detect_salary_fields(headers)
    has_account = "bank_account" in field_indexes
    has_gross = "gross_salary" in field_indexes
    has_deduction = "total_deduction" in field_indexes
    has_net = "net_salary" in field_indexes

    previous_accounts = get_previous_accounts()

    valid_rows = []
    skipped_empty = 0
    cleaned_list = []

    for r in records:
        community = r.get("community", "").strip()
        name = r.get("name", "").strip()
        if not community or not name:
            skipped_empty += 1
            continue
        valid_rows.append(r)

        raw_row = r.get("_raw", {})
        account_raw = ""
        account_clean = ""
        if has_account:
            col_idx = field_indexes["bank_account"]
            header_key = headers[col_idx]
            account_raw = raw_row.get(header_key, "")
            account_clean = clean_account(account_raw)

        gross = None
        deduction = None
        net = None
        if has_gross:
            gross = to_float(raw_row.get(headers[field_indexes["gross_salary"]]))
        if has_deduction:
            deduction = to_float(raw_row.get(headers[field_indexes["total_deduction"]]))
        if has_net:
            net = to_float(raw_row.get(headers[field_indexes["net_salary"]]))

        cleaned_list.append({
            "row": r["_row_index"],
            "community": community,
            "name": name,
            "account_raw": account_raw,
            "account_clean": account_clean,
            "gross": gross,
            "deduction": deduction,
            "net": net,
            "previous": previous_accounts.get((community, name), ""),
        })

    total_valid = len(cleaned_list)

    account_counts = {}
    for c in cleaned_list:
        if c["account_clean"]:
            account_counts[c["account_clean"]] = account_counts.get(c["account_clean"], 0) + 1

    duplicate_groups = []
    seen = set()
    for c in cleaned_list:
        acc = c["account_clean"]
        if not acc or account_counts.get(acc, 0) <= 1 or acc in seen:
            continue
        group_rows = [x for x in cleaned_list if x["account_clean"] == acc]
        if len(group_rows) <= 1:
            continue
        duplicate_groups.append({
            "account": acc,
            "count": len(group_rows),
            "rows": group_rows,
        })
        seen.add(acc)

    math_mismatch = []
    for c in cleaned_list:
        if c["gross"] is None or c["deduction"] is None or c["net"] is None:
            continue
        diff = round(c["gross"] - c["deduction"] - c["net"], 2)
        if abs(diff) > 0.02:
            math_mismatch.append({
                "row": c["row"],
                "community": c["community"],
                "name": c["name"],
                "gross": c["gross"],
                "deduction": c["deduction"],
                "net": c["net"],
                "diff": diff,
            })

    account_changed = []
    for c in cleaned_list:
        key = (c["community"], c["name"])
        prev = previous_accounts.get(key)
        cur = c["account_clean"]
        if prev and cur and prev != cur:
            account_changed.append({
                "row": c["row"],
                "community": c["community"],
                "name": c["name"],
                "previous": prev,
                "current": cur,
            })

    account_empty = []
    for c in cleaned_list:
        if not c["account_clean"]:
            account_empty.append({
                "row": c["row"],
                "community": c["community"],
                "name": c["name"],
                "account_raw": c["account_raw"],
            })

    return {
        "total_parsed": len(records),
        "skipped_empty": skipped_empty,
        "total_valid": total_valid,
        "has_account_field": has_account,
        "has_math_fields": has_gross and has_deduction and has_net,
        "duplicate_account_groups": duplicate_groups,
        "math_mismatch_issues": math_mismatch,
        "account_changed_groups": account_changed,
        "account_empty_issues": account_empty,
        "has_previous_data": len(previous_accounts) > 0,
        "all_rows": cleaned_list,
    }


def build_preview_rows(df, detected_header, head_data=5, tail_data=5, threshold=10, full_preview=False):
    """构造预览行：表头 + 前 N 条数据 + 后 N 条数据；数据 ≤ threshold 则全显。
    当 full_preview=True 时返回全部行。
    当需要折叠时，中间插入一个省略标记行。
    返回 (preview_rows, row_indices)，row_indices 对应 Excel 行号（1-based），省略行为 None。
    """
    header_count = (detected_header + 1) if detected_header is not None else 1
    data_count = max(0, len(df) - header_count)

    def row_values(i):
        return [normalize_text(v) for v in df.iloc[i].tolist()]

    preview = []
    row_indices = []
    for i in range(header_count):
        preview.append(row_values(i))
        row_indices.append(i + 1)

    if full_preview or data_count <= threshold:
        for i in range(header_count, len(df)):
            preview.append(row_values(i))
            row_indices.append(i + 1)
    else:
        for i in range(header_count, header_count + head_data):
            preview.append(row_values(i))
            row_indices.append(i + 1)
        omitted = data_count - head_data - tail_data
        preview.append([f"... 中间省略 {omitted} 行数据 ..."])
        row_indices.append(None)
        for i in range(len(df) - tail_data, len(df)):
            preview.append(row_values(i))
            row_indices.append(i + 1)

    return preview, row_indices


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/preview", methods=["POST"])
def api_preview():
    if "file" not in request.files:
        return jsonify({"error": "请选择文件"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "请选择文件"}), 400
    file_id, path = save_upload(file)
    try:
        xl = pd.ExcelFile(path)
        sheets = xl.sheet_names
        default_sheet = sheets[0]
        df = read_excel_raw(path, default_sheet)
        file_type = request.form.get("file_type", "salary")
        required = ["姓名", "社区"] if file_type == "salary" else ["姓名", "社区", "邮箱"]
        detected_header = detect_header_row(df, required)
        full_preview = request.form.get("full_preview", "false").lower() in ("true", "1", "yes")
        preview, preview_row_indices = build_preview_rows(df, detected_header, head_data=5, tail_data=5, threshold=10, full_preview=full_preview)
        detected_year, detected_month = None, None
        if file_type == "salary":
            detected_year, detected_month = extract_salary_date(df)
            # 表格未识别到日期时，尝试从文件名提取
            if detected_year is None:
                detected_year, detected_month = extract_date_from_filename(file.filename)
        return jsonify({
            "file_id": file_id,
            "filename": file.filename,
            "sheets": sheets,
            "default_sheet": default_sheet,
            "preview": preview,
            "preview_row_indices": preview_row_indices,
            "shape": {"rows": int(df.shape[0]), "cols": int(df.shape[1])},
            "detected_header_row": detected_header,
            "detected_year": detected_year,
            "detected_month": detected_month,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sheet-preview", methods=["POST"])
def api_sheet_preview():
    data = request.get_json() or {}
    file_id = data.get("file_id")
    sheet_name = data.get("sheet_name")
    file_type = data.get("file_type", "salary")
    if not file_id or not sheet_name:
        return jsonify({"error": "缺少参数"}), 400
    path = UPLOAD_DIR / file_id
    if not path.exists():
        return jsonify({"error": "文件不存在，请重新上传"}), 400
    try:
        df = read_excel_raw(path, sheet_name)
        required = ["姓名", "社区"] if file_type == "salary" else ["姓名", "社区", "邮箱"]
        detected_header = detect_header_row(df, required)
        full_preview = bool(data.get("full_preview", False))
        preview, preview_row_indices = build_preview_rows(df, detected_header, head_data=5, tail_data=5, threshold=10, full_preview=full_preview)
        detected_year, detected_month = None, None
        if file_type == "salary":
            detected_year, detected_month = extract_salary_date(df)
            if detected_year is None:
                # sheet-preview 没有 filename，这里不处理文件名日期，保持原逻辑
                pass
        return jsonify({
            "preview": preview,
            "preview_row_indices": preview_row_indices,
            "shape": {"rows": int(df.shape[0]), "cols": int(df.shape[1])},
            "detected_header_row": detected_header,
            "detected_year": detected_year,
            "detected_month": detected_month,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/parse-config", methods=["POST"])
def api_parse_config():
    """根据用户指定的表头/数据范围解析记录，并给出列映射建议。"""
    data = request.get_json() or {}
    file_id = data.get("file_id")
    sheet_name = data.get("sheet_name")
    file_type = data.get("file_type", "salary")
    header_rows = data.get("header_rows", [0])
    data_start_row = data.get("data_start_row", 1)
    data_end_row = data.get("data_end_row")
    data_end_col = data.get("data_end_col")
    skip_rows = data.get("skip_rows", [])
    salary_field_mapping = data.get("salary_field_mapping") or {}

    if not file_id or not sheet_name:
        return jsonify({"error": "缺少参数"}), 400
    path = UPLOAD_DIR / file_id
    if not path.exists():
        return jsonify({"error": "文件不存在"}), 400

    try:
        df = read_excel_raw(path, sheet_name)
        df = apply_column_limit(df, data_end_col)
        # 转换为 0-based
        header_rows = [int(r) - 1 for r in header_rows]
        data_start_row = int(data_start_row) - 1
        data_end_row = int(data_end_row) if data_end_row else None
        if data_end_row is not None:
            data_end_row = int(data_end_row)

        headers = fill_merged_headers(df, header_rows)
        candidates = {
            "name": ["姓名"],
            "community": ["社区"],
        }
        if file_type == "email":
            candidates["email"] = ["邮箱", "邮箱号", "电子邮箱", "邮件"]
        mapping = suggest_column_mapping(headers, candidates)

        records, _ = parse_records(
            df, header_rows, data_start_row, data_end_row, mapping, skip_rows=skip_rows, is_email=(file_type == "email")
        )

        # 邮箱校验预览
        validation = None
        if file_type == "email" and "email" in mapping:
            # 先清洗所有邮箱并收集重复
            cleaned_records = []
            for r in records:
                email, status, errs = validate_email(r.get("email", ""), set())
                cleaned_records.append({
                    "row": r["_row_index"],
                    "community": r.get("community", ""),
                    "name": r.get("name", ""),
                    "raw": r.get("email", ""),
                    "cleaned": email,
                    "errors": errs,
                })

            # 统计每个清洗后邮箱出现次数
            email_counts = {}
            for cr in cleaned_records:
                email_counts[cr["cleaned"]] = email_counts.get(cr["cleaned"], 0) + 1

            duplicate_groups = []
            issues = []
            valid_count = 0
            duplicate_count = 0
            for cr in cleaned_records:
                is_duplicate = email_counts.get(cr["cleaned"], 0) > 1 and "格式错误" not in cr["errors"] and "缺少 @" not in cr["errors"]
                if is_duplicate:
                    duplicate_count += 1
                    continue
                if cr["errors"]:
                    issues.append(cr)
                else:
                    valid_count += 1

            # 构建重复组（仅包含出现次数 > 1 且格式正确的邮箱）
            seen_emails = set()
            for email, count in email_counts.items():
                if count <= 1:
                    continue
                rows = [cr for cr in cleaned_records if cr["cleaned"] == email]
                if not rows:
                    continue
                # 若该邮箱本身存在格式错误，则归到 issues 而不是重复组
                if any("格式错误" in r["errors"] or "缺少 @" in r["errors"] for r in rows):
                    continue
                duplicate_groups.append({
                    "email": email,
                    "count": count,
                    "rows": rows,
                })
                seen_emails.add(email)

            validation = {
                "total": len(records),
                "valid": valid_count,
                "invalid": duplicate_count + len(issues),
                "duplicate_count": duplicate_count,
                "other_issue_count": len(issues),
                "issues": issues[:50],
                "duplicate_groups": duplicate_groups,
            }

        if file_type == "salary":
            validation = validate_salary_records(records, headers, field_indexes_override=salary_field_mapping)

        resp = {
            "headers": headers,
            "suggested_mapping": mapping,
            "record_count": len(records),
            "sample": records[:5],
            "validation": validation,
        }
        if file_type == "salary":
            # 返回自动检测到的工资字段列索引，供前端预填下拉框
            detected_indexes = detect_salary_fields(headers)
            resp["detected_salary_fields"] = detected_indexes
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/import", methods=["POST"])
def api_import():
    data = request.get_json() or {}
    salary_cfg = data.get("salary") or {}
    email_cfg = data.get("email") or {}
    year = data.get("year")
    month = data.get("month")

    if not salary_cfg or not email_cfg:
        return jsonify({"error": "请同时配置工资表和邮箱表"}), 400
    if year is None or month is None:
        return jsonify({"error": "请选择工资单年月"}), 400

    salary_path = UPLOAD_DIR / salary_cfg["file_id"]
    email_path = UPLOAD_DIR / email_cfg["file_id"]
    if not salary_path.exists() or not email_path.exists():
        return jsonify({"error": "上传文件已失效，请重新上传"}), 400

    try:
        # 解析邮箱
        edf = read_excel_raw(email_path, email_cfg["sheet_name"])
        edf = apply_column_limit(edf, email_cfg.get("data_end_col"))
        e_header_rows = [int(r) - 1 for r in email_cfg.get("header_rows", [1])]
        e_data_start = int(email_cfg.get("data_start_row", 2)) - 1
        e_data_end = int(email_cfg["data_end_row"]) if email_cfg.get("data_end_row") else None
        e_mapping = email_cfg.get("mapping", {})
        email_records, _ = parse_records(edf, e_header_rows, e_data_start, e_data_end, e_mapping, skip_rows=email_cfg.get("skip_rows", []), is_email=True)

        # 应用用户在前端修正的邮箱覆盖
        overrides = email_cfg.get("overrides") or {}

        # 校验并构建 (community, name) -> email info
        seen_emails = set()
        email_map = {}
        email_issues = []
        for r in email_records:
            key = (r.get("community", ""), r.get("name", ""))
            if not key[0] or not key[1]:
                continue
            raw_email = overrides.get(f"{key[0]}|{key[1]}", r.get("email", ""))
            email, status, errs = validate_email(raw_email, seen_emails)
            seen_emails.add(email)
            email_map[key] = {
                "email": email,
                "status": status,
                "issues": ";".join(errs),
            }
            if errs:
                email_issues.append({
                    "community": key[0],
                    "name": key[1],
                    "raw": r.get("email", ""),
                    "cleaned": email,
                    "errors": errs,
                })

        # 解析工资表
        sdf = read_excel_raw(salary_path, salary_cfg["sheet_name"])
        sdf = apply_column_limit(sdf, salary_cfg.get("data_end_col"))
        s_header_rows = [int(r) - 1 for r in salary_cfg.get("header_rows", [3])]
        s_data_start = int(salary_cfg.get("data_start_row", 5)) - 1
        s_data_end = int(salary_cfg["data_end_row"]) if salary_cfg.get("data_end_row") else None
        s_mapping = salary_cfg.get("mapping", {})
        salary_records, salary_headers = parse_records(
            sdf, s_header_rows, s_data_start, s_data_end, s_mapping, skip_rows=salary_cfg.get("skip_rows", []), is_email=False
        )

        # 优先使用用户手动指定的工资字段列索引，否则自动检测
        user_field_mapping = salary_cfg.get("salary_field_mapping") or {}
        field_indexes = {}
        for k, v in user_field_mapping.items():
            if v is not None and v != "":
                try:
                    field_indexes[k] = int(v)
                except (TypeError, ValueError):
                    pass
        if not field_indexes:
            field_indexes = detect_salary_fields(salary_headers)

        salary_overrides = salary_cfg.get("overrides") or {}

        # 写入数据库
        with get_db() as conn:
            cur = conn.execute(
                "INSERT OR REPLACE INTO salary_imports (year, month, salary_filename, email_filename, imported_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(year), int(month), salary_cfg.get("filename", ""), email_cfg.get("filename", ""), datetime.datetime.now().isoformat())
            )
            import_id = cur.lastrowid

            stats = {"total_salary": 0, "matched": 0, "unmatched": 0, "email_invalid": 0}
            unmatched = []
            for r in salary_records:
                community = r.get("community", "").strip()
                name = r.get("name", "").strip()
                if not community or not name:
                    continue
                stats["total_salary"] += 1
                key = (community, name)
                email_info = email_map.get(key, {"email": "", "status": "missing", "issues": "未找到对应邮箱"})
                if email_info["status"] != "valid":
                    stats["email_invalid"] += 1
                if email_info["status"] == "missing":
                    stats["unmatched"] += 1
                    unmatched.append({"community": community, "name": name})
                    continue
                stats["matched"] += 1

                cur = conn.execute(
                    "INSERT OR REPLACE INTO persons (import_id, community, name, email, email_status, email_issues) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (import_id, community, name, email_info["email"], email_info["status"], email_info["issues"])
                )
                person_id = cur.lastrowid

                raw_row = r.get("_raw", {})
                row_index = str(r.get("_row_index", ""))
                ov = salary_overrides.get(row_index) or {}

                gross = ov.get("gross")
                if gross is None and "gross_salary" in field_indexes:
                    gross = to_float(raw_row.get(salary_headers[field_indexes["gross_salary"]]))

                total_deduction = ov.get("deduction")
                if total_deduction is None and "total_deduction" in field_indexes:
                    total_deduction = to_float(raw_row.get(salary_headers[field_indexes["total_deduction"]]))

                net = ov.get("net")
                if net is None and "net_salary" in field_indexes:
                    net = to_float(raw_row.get(salary_headers[field_indexes["net_salary"]]))

                bank_raw = raw_row.get(salary_headers[field_indexes["bank_account", -1]]) if "bank_account" in field_indexes else None
                if ov.get("account_clean"):
                    bank = str(ov["account_clean"])
                elif bank_raw:
                    bank = clean_account(bank_raw)
                else:
                    bank = None

                conn.execute(
                    "INSERT INTO salary_records (import_id, person_id, data_json, gross_salary, total_deduction, net_salary, bank_account) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (import_id, person_id, json.dumps(raw_row, ensure_ascii=False), gross, total_deduction, net, bank)
                )

        return jsonify({
            "success": True,
            "import_id": import_id,
            "year": year,
            "month": month,
            "stats": stats,
            "unmatched": unmatched[:50],
            "email_issues": email_issues[:50],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/months", methods=["GET"])
def api_months():
    with get_db() as conn:
        rows = conn.execute("SELECT DISTINCT year, month FROM salary_imports ORDER BY year DESC, month DESC").fetchall()
    return jsonify([{"year": r["year"], "month": r["month"], "label": f"{r['year']}年{r['month']}月"} for r in rows])


def get_latest_month(conn):
    row = conn.execute("SELECT year, month FROM salary_imports ORDER BY year DESC, month DESC LIMIT 1").fetchone()
    return (row["year"], row["month"]) if row else (None, None)


@app.route("/api/search-suggest", methods=["GET"])
def api_search_suggest():
    """按姓名模糊匹配，返回下拉建议列表（含岗位等摘要信息）。未传月份时取最新月份。"""
    q = request.args.get("q", "").strip()
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if not q:
        return jsonify([])

    with get_db() as conn:
        if not (year and month):
            year, month = get_latest_month(conn)
        if not (year and month):
            return jsonify([])
        rows = conn.execute(
            """
            SELECT p.id, p.community, p.name,
                   s.year, s.month, sr.data_json
            FROM persons p
            JOIN salary_imports s ON p.import_id = s.id
            JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
            WHERE p.name LIKE ? AND s.year = ? AND s.month = ?
            ORDER BY p.community, p.name
            """,
            (f"%{q}%", year, month)
        ).fetchall()

    seen = set()
    results = []
    for r in rows:
        key = (r["community"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        detail = json.loads(r["data_json"] or "{}")
        summary = extract_person_summary(detail)
        results.append({
            "id": r["id"],
            "community": r["community"],
            "name": r["name"],
            "position": summary.get("position", ""),
            "service_years": summary.get("service_years", ""),
            "position_start_date": summary.get("position_start_date", ""),
        })
    return jsonify(results[:20])


@app.route("/api/search", methods=["GET"])
def api_search():
    q = request.args.get("q", "").strip()
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    with get_db() as conn:
        # 空姓名搜索默认取最新月份；有姓名但未指定月份时返回所有月份
        if not q and not (year and month):
            year, month = get_latest_month(conn)
        if not q and not (year and month):
            return jsonify([])

        if q:
            if year and month:
                rows = conn.execute(
                    """
                    SELECT p.id, p.community, p.name, p.email, p.email_status, p.email_issues,
                           s.year, s.month, sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
                    FROM persons p
                    JOIN salary_imports s ON p.import_id = s.id
                    JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
                    WHERE p.name LIKE ? AND s.year = ? AND s.month = ?
                    ORDER BY p.community, p.name
                    """,
                    (f"%{q}%", year, month)
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT p.id, p.community, p.name, p.email, p.email_status, p.email_issues,
                           s.year, s.month, sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
                    FROM persons p
                    JOIN salary_imports s ON p.import_id = s.id
                    JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
                    WHERE p.name LIKE ?
                    ORDER BY s.year DESC, s.month DESC, p.community, p.name
                    """,
                    (f"%{q}%",)
                ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT p.id, p.community, p.name, p.email, p.email_status, p.email_issues,
                       s.year, s.month, sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
                FROM persons p
                JOIN salary_imports s ON p.import_id = s.id
                JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
                WHERE s.year = ? AND s.month = ?
                ORDER BY p.community, p.name
                """,
                (year, month)
            ).fetchall()

    results = []
    seen = set()
    for r in rows:
        # 按月份筛选时同一人只取一条；跨月份搜索时每条月份记录都保留
        key = (r["community"], r["name"], r["year"], r["month"]) if q and not (year and month) else (r["community"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        data = json.loads(r["data_json"] or "{}")
        results.append({
            "id": r["id"],
            "community": r["community"],
            "name": r["name"],
            "email": r["email"],
            "email_status": r["email_status"],
            "email_issues": r["email_issues"],
            "year": r["year"],
            "month": r["month"],
            "gross_salary": r["gross_salary"],
            "total_deduction": r["total_deduction"],
            "net_salary": r["net_salary"],
            "bank_account": r["bank_account"],
            "detail": data,
        })
    return jsonify(results)


@app.route("/api/person/<int:person_id>", methods=["GET"])
def api_person(person_id):
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    with get_db() as conn:
        if not (year and month):
            year, month = get_latest_month(conn)
        if not (year and month):
            return jsonify({"error": "未找到"}), 404
        rows = conn.execute(
            """
            SELECT p.id, p.community, p.name, p.email, p.email_status, p.email_issues,
                   s.year, s.month, sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
            FROM persons p
            JOIN salary_imports s ON p.import_id = s.id
            JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
            WHERE p.id = ? AND s.year = ? AND s.month = ?
            ORDER BY s.year DESC, s.month DESC
            """,
            (person_id, year, month)
        ).fetchall()
    if not rows:
        return jsonify({"error": "未找到"}), 404
    history = []
    for r in rows:
        history.append({
            "year": r["year"],
            "month": r["month"],
            "gross_salary": r["gross_salary"],
            "total_deduction": r["total_deduction"],
            "net_salary": r["net_salary"],
            "bank_account": r["bank_account"],
            "detail": json.loads(r["data_json"] or "{}"),
        })
    first = rows[0]
    return jsonify({
        "id": first["id"],
        "community": first["community"],
        "name": first["name"],
        "email": first["email"],
        "email_status": first["email_status"],
        "email_issues": first["email_issues"],
        "history": history,
    })


@app.route("/api/imports", methods=["GET"])
def api_imports():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, year, month, salary_filename, email_filename, imported_at FROM salary_imports ORDER BY year DESC, month DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/import/<int:import_id>", methods=["DELETE"])
def api_delete_import(import_id):
    with get_db() as conn:
        conn.execute("DELETE FROM salary_imports WHERE id = ?", (import_id,))
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# User config (personalized import defaults)
# ---------------------------------------------------------------------------

DEFAULT_USER_CONFIG = {
    "salary": {
        "skip_rows": "",
        "header_rows": "3,4",
        "data_start": "5",
        "data_end": "",
        "data_end_col": "",
    },
    "email": {
        "skip_rows": "",
        "header_rows": "2",
        "data_start": "3",
        "data_end": "",
        "data_end_col": "",
    },
}


def load_user_config():
    if not USER_CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_USER_CONFIG))
    try:
        data = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
        merged = json.loads(json.dumps(DEFAULT_USER_CONFIG))
        for section in ("salary", "email"):
            if section in data and isinstance(data[section], dict):
                merged[section].update(data[section])
        return merged
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(DEFAULT_USER_CONFIG))


def save_user_config(cfg):
    USER_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/api/user-config", methods=["GET"])
def api_get_user_config():
    return jsonify(load_user_config())


@app.route("/api/user-config", methods=["PUT"])
def api_put_user_config():
    data = request.get_json() or {}
    current = load_user_config()
    updated = {
        "salary": {**current["salary"], **(data.get("salary") or {})},
        "email": {**current["email"], **(data.get("email") or {})},
    }
    save_user_config(updated)
    return jsonify(updated)


if not USER_CONFIG_PATH.exists():
    save_user_config(json.loads(json.dumps(DEFAULT_USER_CONFIG)))


# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

def _open_dir(path: Path):
    """使用系统默认方式打开目录。"""
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", str(path)], check=True)
    elif system == "Windows":
        subprocess.run(["explorer", str(path)], check=True)
    else:
        subprocess.run(["xdg-open", str(path)], check=True)


@app.route("/api/open-project-dir", methods=["POST"])
def api_open_project_dir():
    """打开项目文件所在目录。"""
    try:
        _open_dir(BASE_DIR)
        return jsonify({"opened": str(BASE_DIR)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/package-project", methods=["POST"])
def api_package_project():
    """将项目目录打包为 zip，支持仅代码 / 带数据库文件 / 带运行环境三种模式。"""
    data = request.get_json() or {}
    # 兼容旧版 include_db 参数，新版优先使用 mode
    mode = data.get("mode")
    if not mode and data.get("include_db"):
        mode = "with_db"
    mode = mode or "code"

    include_db = mode in ("with_db", "with_runtime")
    include_runtime = mode == "with_runtime"

    packages_dir = BASE_DIR / "packages"
    packages_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = {"with_db": "_with_db", "with_runtime": "_with_runtime"}.get(mode, "")
    zip_path = packages_dir / f"salary_manager_{timestamp}{suffix}.zip"

    exclude_dirs = {"__pycache__", "logs", ".git", "packages", ".idea", ".aicoding-chat-workspace", "uploads"}
    if not include_runtime:
        exclude_dirs.add("venv")
    exclude_files = {".DS_Store", "Thumbs.db", ".app.pid"}
    if include_db:
        exclude_suffixes = (".db-wal", ".db-shm", ".log")
    else:
        exclude_suffixes = (".db-wal", ".db-shm", ".db", ".log")
    exclude_name_suffixes = (".zip", ".tmp", ".temp")
    exclude_name_patterns = (".wip.", ".bak.")

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in BASE_DIR.rglob("*"):
                rel_parts = path.relative_to(BASE_DIR).parts
                if any(part in exclude_dirs for part in rel_parts):
                    continue
                if not path.is_file():
                    continue
                name = path.name
                if name in exclude_files:
                    continue
                lower_name = name.lower()
                if any(lower_name.endswith(suffix) for suffix in exclude_suffixes):
                    continue
                if any(lower_name.endswith(suffix) for suffix in exclude_name_suffixes):
                    continue
                if any(pattern in name for pattern in exclude_name_patterns):
                    continue
                zf.write(path, path.relative_to(BASE_DIR))

        _open_dir(packages_dir)
        return jsonify({"zip": str(zip_path), "opened": str(packages_dir), "mode": mode})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stop-service", methods=["POST"])
def api_stop_service():
    """调用 stop.sh 停止服务。"""
    try:
        subprocess.Popen(
            ["bash", str(BASE_DIR / "stop.sh")],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return jsonify({"message": "服务正在停止，请稍后刷新页面"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

@app.route("/static/<path:path>")
def send_static(path):
    return send_from_directory(BASE_DIR / "static", path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)