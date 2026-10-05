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
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file

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
        email_issues TEXT
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
        # 迁移：旧版 persons 表有 UNIQUE(import_id, community, name) 约束，
        # 会导致同社区同名的工资记录被覆盖。检测并移除该约束。
        cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='persons'")
        row = cur.fetchone()
        if row and "UNIQUE(import_id, community, name)" in (row["sql"] or ""):
            conn.executescript("""
                CREATE TABLE persons_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_id INTEGER NOT NULL REFERENCES salary_imports(id) ON DELETE CASCADE,
                    community TEXT NOT NULL,
                    name TEXT NOT NULL,
                    email TEXT,
                    email_status TEXT NOT NULL DEFAULT 'unknown',
                    email_issues TEXT
                );
                INSERT INTO persons_new SELECT * FROM persons;
                DROP TABLE persons;
                ALTER TABLE persons_new RENAME TO persons;
                CREATE INDEX IF NOT EXISTS idx_persons_name ON persons(name);
                CREATE INDEX IF NOT EXISTS idx_persons_community ON persons(community);
                CREATE INDEX IF NOT EXISTS idx_persons_import ON persons(import_id);
            """)


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


def apply_email_row_override(record, overrides):
    """取邮箱表某一行的有效值：用户在前端按行号覆盖的社区/姓名/邮箱优先于原始值。

    按行号而非「社区|姓名」寻址，因为重名人员的键会互相覆盖。
    """
    ov = overrides.get(str(record.get("_row_index"))) or {}

    def pick(key, fallback):
        value = ov.get(key)
        return fallback if value is None else str(value)

    community = pick("community", record.get("community", "")).strip()
    name = pick("name", record.get("name", "")).strip()
    raw_email = pick("email", record.get("email", ""))
    return community, name, raw_email


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
            if col_idx is None or not isinstance(col_idx, int) or col_idx < 0 or col_idx >= len(row):
                record[key] = ""
            else:
                record[key] = normalize_text(row[col_idx])
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
        "bank_account": ["银行卡号", "领款人帐号", "领款人账号", "银行账号", "账号", "卡号"],
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

    # 检测社区+姓名重复
    name_counts = {}
    for c in cleaned_list:
        key = (c["community"], c["name"])
        name_counts[key] = name_counts.get(key, 0) + 1
    duplicate_name_groups = []
    seen_names = set()
    for c in cleaned_list:
        key = (c["community"], c["name"])
        if name_counts.get(key, 0) <= 1 or key in seen_names:
            continue
        group_rows = [x for x in cleaned_list if (x["community"], x["name"]) == key]
        duplicate_name_groups.append({
            "community": key[0],
            "name": key[1],
            "count": len(group_rows),
            "rows": [{"row": x["row"], "community": x["community"], "name": x["name"]} for x in group_rows],
        })
        seen_names.add(key)

    return {
        "total_parsed": len(records),
        "skipped_empty": skipped_empty,
        "total_valid": total_valid,
        "has_account_field": has_account,
        "has_math_fields": has_gross and has_deduction and has_net,
        "duplicate_account_groups": duplicate_groups,
        "duplicate_name_groups": duplicate_name_groups,
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


@app.route("/api/download/<file_id>")
def api_download(file_id):
    """下载已上传的文件，用于在确认导入页打开原始 Excel。"""
    path = UPLOAD_DIR / file_id
    if not path.exists():
        return jsonify({"error": "文件不存在"}), 404
    filename = request.args.get("filename", path.name)
    return send_file(str(path), as_attachment=True, download_name=filename)


@app.route("/api/history-emails")
def api_history_emails():
    """根据社区+姓名查询历史邮箱列表。"""
    community = (request.args.get("community") or "").strip()
    name = (request.args.get("name") or "").strip()
    if not community or not name:
        return jsonify({"emails": []})
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT email FROM persons "
            "WHERE community = ? AND name = ? AND email != '' AND email IS NOT NULL "
            "ORDER BY id DESC",
            (community, name)
        ).fetchall()
    emails = [r[0] for r in rows]
    return jsonify({"emails": emails})


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
                is_duplicate = cr["cleaned"] and email_counts.get(cr["cleaned"], 0) > 1 and "格式错误" not in cr["errors"] and "缺少 @" not in cr["errors"]
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
                if count <= 1 or not email:
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

            # 检测邮箱表中社区+姓名重复
            email_name_counts = {}
            for cr in cleaned_records:
                key = (cr["community"], cr["name"])
                email_name_counts[key] = email_name_counts.get(key, 0) + 1
            duplicate_name_groups = []
            seen_email_names = set()
            for cr in cleaned_records:
                key = (cr["community"], cr["name"])
                if email_name_counts.get(key, 0) <= 1 or key in seen_email_names:
                    continue
                group_rows = [x for x in cleaned_records if (x["community"], x["name"]) == key]
                duplicate_name_groups.append({
                    "community": key[0],
                    "name": key[1],
                    "count": len(group_rows),
                    "rows": [{"row": x["row"], "community": x["community"], "name": x["name"], "email": x.get("cleaned", "")} for x in group_rows],
                })
                seen_email_names.add(key)

            validation = {
                "total": len(records),
                "valid": valid_count,
                "invalid": duplicate_count + len(issues),
                "duplicate_count": duplicate_count,
                "other_issue_count": len(issues),
                "issues": issues,
                "duplicate_groups": duplicate_groups,
                "duplicate_name_groups": duplicate_name_groups,
                # 全量清洗行：前端据此在本地重算校验结果，用户改完无需重新上传
                "all_rows": cleaned_records,
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


@app.route("/api/import-preview", methods=["POST"])
def api_import_preview():
    """合并工资表与邮箱表（按社区+姓名），返回二维数组供前端预览，不写入数据库。"""
    data = request.get_json() or {}
    salary_cfg = data.get("salary") or {}
    email_cfg = data.get("email") or {}

    if not salary_cfg or not email_cfg:
        return jsonify({"error": "请同时配置工资表和邮箱表"}), 400

    salary_path = UPLOAD_DIR / salary_cfg["file_id"]
    email_path = UPLOAD_DIR / email_cfg["file_id"]
    if not salary_path.exists() or not email_path.exists():
        return jsonify({"error": "上传文件已失效，请重新上传"}), 400

    try:
        # 解析邮箱表
        edf = read_excel_raw(email_path, email_cfg["sheet_name"])
        edf = apply_column_limit(edf, email_cfg.get("data_end_col"))
        e_header_rows = [int(r) - 1 for r in email_cfg.get("header_rows", [1])]
        e_data_start = int(email_cfg.get("data_start_row", 2)) - 1
        e_data_end = int(email_cfg["data_end_row"]) if email_cfg.get("data_end_row") else None
        e_mapping = email_cfg.get("mapping", {})
        email_records, _ = parse_records(edf, e_header_rows, e_data_start, e_data_end, e_mapping,
                                         skip_rows=email_cfg.get("skip_rows", []), is_email=True)

        overrides = email_cfg.get("overrides") or {}
        email_deleted_rows = set(email_cfg.get("deleted_rows") or [])
        email_map = {}
        for r in email_records:
            if r.get("_row_index") in email_deleted_rows:
                continue
            community, name, raw_email = apply_email_row_override(r, overrides)
            if not community or not name:
                continue
            email, _, _ = validate_email(raw_email, set())
            email_map[(community, name)] = email

        # 解析工资表
        sdf = read_excel_raw(salary_path, salary_cfg["sheet_name"])
        sdf = apply_column_limit(sdf, salary_cfg.get("data_end_col"))
        s_header_rows = [int(r) - 1 for r in salary_cfg.get("header_rows", [3])]
        s_data_start = int(salary_cfg.get("data_start_row", 5)) - 1
        s_data_end = int(salary_cfg["data_end_row"]) if salary_cfg.get("data_end_row") else None
        s_mapping = salary_cfg.get("mapping", {})
        salary_records, salary_headers = parse_records(
            sdf, s_header_rows, s_data_start, s_data_end, s_mapping,
            skip_rows=salary_cfg.get("skip_rows", []), is_email=False
        )

        salary_overrides = salary_cfg.get("overrides") or {}
        salary_deleted_rows = set(salary_cfg.get("deleted_rows") or [])

        headers = list(salary_headers) + ["邮箱"]
        rows = []
        row_indices = []
        matched = 0
        unmatched = 0
        for r in salary_records:
            row_index = r.get("_row_index", "")
            if row_index in salary_deleted_rows:
                continue
            community = r.get("community", "").strip()
            name = r.get("name", "").strip()
            # 应用社区/姓名覆盖
            ov_identity = salary_overrides.get(str(row_index)) or {}
            if ov_identity.get("community") is not None:
                community = str(ov_identity["community"]).strip()
            if ov_identity.get("name") is not None:
                name = str(ov_identity["name"]).strip()
            if not community or not name:
                continue
            raw = r.get("_raw", {})
            row_values = [raw.get(h, "") for h in salary_headers]
            # 将社区/姓名覆盖应用到返回的行数据中
            if ov_identity.get("community") is not None:
                ci = s_mapping.get("community")
                if ci is not None and ci < len(row_values):
                    row_values[ci] = ov_identity["community"]
            if ov_identity.get("name") is not None:
                ni = s_mapping.get("name")
                if ni is not None and ni < len(row_values):
                    row_values[ni] = ov_identity["name"]
            email = email_map.get((community, name), "")
            if email:
                matched += 1
            else:
                unmatched += 1
            row_values.append(email)
            rows.append(row_values)
            row_indices.append(row_index)

        return jsonify({
            "headers": headers,
            "rows": rows,
            "row_indices": row_indices,
            "total": len(rows),
            "matched": matched,
            "unmatched": unmatched,
        })
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
        email_deleted_rows = set(email_cfg.get("deleted_rows") or [])

        # 校验并构建 (community, name) -> email info
        seen_emails = set()
        email_map = {}
        email_issues = []
        for r in email_records:
            if r.get("_row_index") in email_deleted_rows:
                continue
            community, name, raw_email = apply_email_row_override(r, overrides)
            key = (community, name)
            if not key[0] or not key[1]:
                continue
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
                    "raw": raw_email,
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
        salary_deleted_rows = set(salary_cfg.get("deleted_rows") or [])
        email_overrides = salary_cfg.get("email_overrides") or {}

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
                row_index = r.get("_row_index", "")
                # 跳过用户删除的行
                if row_index in salary_deleted_rows:
                    continue
                community = r.get("community", "").strip()
                name = r.get("name", "").strip()
                # 应用社区/姓名覆盖
                ov_for_identity = salary_overrides.get(str(row_index)) or {}
                if ov_for_identity.get("community") is not None:
                    community = str(ov_for_identity["community"]).strip()
                if ov_for_identity.get("name") is not None:
                    name = str(ov_for_identity["name"]).strip()
                if not community or not name:
                    continue
                stats["total_salary"] += 1
                key = (community, name)
                # 优先使用用户在确认导入页手动填写的邮箱
                manual_email = email_overrides.get(str(row_index))
                if manual_email is not None and manual_email.strip():
                    email_info = {"email": manual_email.strip(), "status": "valid", "issues": ""}
                else:
                    email_info = email_map.get(key, {"email": "", "status": "missing", "issues": "未找到对应邮箱"})
                if email_info["status"] != "valid":
                    stats["email_invalid"] += 1
                if email_info["status"] == "missing":
                    stats["unmatched"] += 1
                    unmatched.append({"community": community, "name": name})
                else:
                    stats["matched"] += 1

                # 无论是否匹配到邮箱，都写入人员和工资记录；未匹配的邮箱留空
                cur = conn.execute(
                    "INSERT INTO persons (import_id, community, name, email, email_status, email_issues) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (import_id, community, name, email_info["email"], email_info["status"], email_info["issues"])
                )
                person_id = cur.lastrowid

                raw_row = r.get("_raw", {})
                ov = salary_overrides.get(str(row_index)) or {}

                gross = ov.get("gross")
                if gross is None and "gross_salary" in field_indexes:
                    gross = to_float(raw_row.get(salary_headers[field_indexes["gross_salary"]]))

                total_deduction = ov.get("deduction")
                if total_deduction is None and "total_deduction" in field_indexes:
                    total_deduction = to_float(raw_row.get(salary_headers[field_indexes["total_deduction"]]))

                net = ov.get("net")
                if net is None and "net_salary" in field_indexes:
                    net = to_float(raw_row.get(salary_headers[field_indexes["net_salary"]]))

                bank_raw = raw_row.get(salary_headers[field_indexes["bank_account"]]) if "bank_account" in field_indexes else None
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


@app.route("/api/communities", methods=["GET"])
def api_communities():
    """获取所有社区的列表（去重、排序）。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT community FROM persons WHERE community IS NOT NULL AND community != '' ORDER BY community"
        ).fetchall()
    return jsonify([r["community"] for r in rows])


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
    community = request.args.get("community", "").strip()

    with get_db() as conn:
        # 空姓名搜索默认取最新月份；有姓名但未指定月份时返回所有月份
        if not q and not (year and month):
            year, month = get_latest_month(conn)
        if not q and not (year and month):
            return jsonify([])

        if q:
            if year and month:
                sql = """
                    SELECT p.id, p.community, p.name, p.email, p.email_status, p.email_issues,
                           s.year, s.month, sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
                    FROM persons p
                    JOIN salary_imports s ON p.import_id = s.id
                    JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
                    WHERE p.name LIKE ? AND s.year = ? AND s.month = ?
                """
                params = [f"%{q}%", year, month]
                if community:
                    sql += " AND p.community = ?"
                    params.append(community)
                sql += " ORDER BY p.community, p.name"
                rows = conn.execute(sql, params).fetchall()
            else:
                sql = """
                    SELECT p.id, p.community, p.name, p.email, p.email_status, p.email_issues,
                           s.year, s.month, sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
                    FROM persons p
                    JOIN salary_imports s ON p.import_id = s.id
                    JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
                    WHERE p.name LIKE ?
                """
                params = [f"%{q}%"]
                if community:
                    sql += " AND p.community = ?"
                    params.append(community)
                sql += " ORDER BY s.year DESC, s.month DESC, p.community, p.name"
                rows = conn.execute(sql, params).fetchall()
        else:
            sql = """
                SELECT p.id, p.community, p.name, p.email, p.email_status, p.email_issues,
                       s.year, s.month, sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
                FROM persons p
                JOIN salary_imports s ON p.import_id = s.id
                JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
                WHERE s.year = ? AND s.month = ?
            """
            params = [year, month]
            if community:
                sql += " AND p.community = ?"
                params.append(community)
            sql += " ORDER BY p.community, p.name"
            rows = conn.execute(sql, params).fetchall()

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
    # 先获取该人员的基本信息
    with get_db() as conn:
        base = conn.execute(
            "SELECT id, community, name, email, email_status, email_issues FROM persons WHERE id = ?",
            (person_id,)
        ).fetchone()
    if not base:
        return jsonify({"error": "未找到"}), 404
    
    # 按"社区+姓名"查询所有历史导入记录（不同月份的同名人员会有不同 person_id）
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.community, p.name, p.email, p.email_status, p.email_issues,
                   s.year, s.month, sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
            FROM persons p
            JOIN salary_imports s ON p.import_id = s.id
            LEFT JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = s.id
            WHERE p.community = ? AND p.name = ?
            ORDER BY s.year DESC, s.month DESC, s.id DESC
            """,
            (base["community"], base["name"])
        ).fetchall()
    if not rows:
        return jsonify({"error": "未找到"}), 404
    # 收集所有出现过的列名，按首次出现的顺序排列
    detail_keys = []
    seen_keys = set()
    history = []
    for r in rows:
        detail = json.loads(r["data_json"] or "{}")
        # 旧版导入可能只有 persons 没有 salary_records，用 persons 字段兜底
        if not detail:
            detail = {"社区": r["community"], "姓名": r["name"]}
        # 收集所有列名
        for k in detail:
            if k not in seen_keys:
                seen_keys.add(k)
                detail_keys.append(k)
        history.append({
            "year": r["year"],
            "month": r["month"],
            "gross_salary": r["gross_salary"],
            "total_deduction": r["total_deduction"],
            "net_salary": r["net_salary"],
            "bank_account": r["bank_account"],
            "detail": detail,
        })
    first = rows[0]
    return jsonify({
        "id": first["id"],
        "community": first["community"],
        "name": first["name"],
        "email": first["email"],
        "email_status": first["email_status"],
        "email_issues": first["email_issues"],
        "detail_keys": detail_keys,
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


@app.route("/api/import/<int:import_id>/records", methods=["GET"])
def api_import_records(import_id):
    with get_db() as conn:
        imp = conn.execute(
            "SELECT id, year, month, salary_filename, email_filename, imported_at FROM salary_imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        if not imp:
            return jsonify({"error": "导入记录不存在"}), 404
        rows = conn.execute(
            """
            SELECT p.community, p.name, p.email, p.email_status, p.email_issues,
                   sr.gross_salary, sr.total_deduction, sr.net_salary, sr.bank_account, sr.data_json
            FROM persons p
            LEFT JOIN salary_records sr ON sr.person_id = p.id AND sr.import_id = p.import_id
            WHERE p.import_id = ?
            ORDER BY p.id
            """,
            (import_id,),
        ).fetchall()
    records = []
    detail_keys = []
    all_detail_keys = set()
    for r in rows:
        detail = json.loads(r["data_json"] or "{}")
        # 旧版导入可能只有 persons 没有 salary_records，用 persons 字段兜底
        if not detail:
            detail = {"社区": r["community"], "姓名": r["name"]}
        # 收集所有出现过的列名
        all_detail_keys.update(detail.keys())
        records.append({
            "community": r["community"],
            "name": r["name"],
            "email": r["email"],
            "email_status": r["email_status"],
            "email_issues": r["email_issues"],
            "gross_salary": r["gross_salary"],
            "total_deduction": r["total_deduction"],
            "net_salary": r["net_salary"],
            "bank_account": r["bank_account"],
            "detail": detail,
        })
    # 按首次出现的顺序排列列名
    seen = set()
    for rec in records:
        for k in rec["detail"]:
            if k not in seen:
                seen.add(k)
                detail_keys.append(k)
    return jsonify({
        "id": imp["id"],
        "year": imp["year"],
        "month": imp["month"],
        "salary_filename": imp["salary_filename"],
        "email_filename": imp["email_filename"],
        "imported_at": imp["imported_at"],
        "records": records,
        "detail_keys": detail_keys,
    })


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


@app.route("/api/write-back-salary", methods=["POST"])
def api_write_back_salary():
    """将修正后的工资数据写回源 Excel 文件（同时更新 uploads 副本和 assets 原始文件）。"""
    import sys
    print("=" * 60, file=sys.stderr, flush=True)
    print(">>> WRITE-BACK API CALLED <<<", file=sys.stderr, flush=True)
    print("=" * 60, file=sys.stderr, flush=True)
    
    try:
        import openpyxl
        import shutil
        
        data = request.get_json() or {}
        file_id = data.get("file_id")
        sheet_name = data.get("sheet_name")
        overrides = data.get("overrides", {})
        deleted_rows = data.get("deleted_rows", [])
        salary_cfg = data.get("salary_config", {})
        original_filename = data.get("original_filename")  # 原始文件名

        if not file_id or not sheet_name:
            return jsonify({"error": "缺少必要参数"}), 400

        path = UPLOAD_DIR / file_id
        if not path.exists():
            return jsonify({"error": "源文件不存在"}), 404

        print(f"[WRITE-BACK] Upload file: {path}", file=sys.stderr, flush=True)
        print(f"[WRITE-BACK] Sheet: {sheet_name}", file=sys.stderr, flush=True)
        print(f"[WRITE-BACK] Overrides: {len(overrides)}, Deleted: {len(deleted_rows)}", file=sys.stderr, flush=True)
        if original_filename:
            print(f"[WRITE-BACK] Original filename: {original_filename}", file=sys.stderr, flush=True)

        # 读取原始 DataFrame
        df = read_excel_raw(path, sheet_name)
        print(f"[WRITE-BACK] Original rows: {len(df)}", file=sys.stderr, flush=True)
        
        # 读取配置
        header_rows_str = salary_cfg.get("header_rows", "3,4")
        data_start_str = salary_cfg.get("data_start", "5")
        header_rows = [int(r) - 1 for r in header_rows_str.split(",") if r.strip()]
        data_start_row = int(data_start_str) - 1
        
        # 应用删除
        indices_to_delete = set()
        for row_num in deleted_rows:
            df_idx = row_num - 1
            if data_start_row <= df_idx < len(df):  # 只允许删除数据行
                indices_to_delete.add(df_idx)
        
        # 应用修改
        modified_count = 0
        for row_num_str, fields in overrides.items():
            row_num = int(row_num_str)
            df_idx = row_num - 1
            if data_start_row <= df_idx < len(df) and df_idx not in indices_to_delete:  # 只允许修改数据行
                headers = fill_merged_headers(df, header_rows)
                for field_name, new_value in fields.items():
                    for col_idx, col_name in enumerate(headers):
                        if col_name == field_name:
                            df.iat[df_idx, col_idx] = new_value
                            modified_count += 1
                            break
        
        print(f"[WRITE-BACK] Modified: {modified_count} fields", file=sys.stderr, flush=True)
        
        # 删除行
        if indices_to_delete:
            sorted_indices = sorted(indices_to_delete, reverse=True)
            for idx in sorted_indices:
                df = df.drop(index=df.index[idx])
            df = df.reset_index(drop=True)
            print(f"[WRITE-BACK] Deleted: {len(indices_to_delete)} rows, remaining total: {len(df)}", file=sys.stderr, flush=True)
        
        # 提取纯数据行（跳过行和表头行保持原样不动）
        data_df = df.iloc[data_start_row:].reset_index(drop=True)
        print(f"[WRITE-BACK] Data rows to write: {len(data_df)}", file=sys.stderr, flush=True)
        
        # 用 openpyxl 直接操作
        print(f"[WRITE-BACK] Loading workbook...", file=sys.stderr, flush=True)
        wb = openpyxl.load_workbook(path)
        print(f"[WRITE-BACK] Sheets: {wb.sheetnames}", file=sys.stderr, flush=True)
        
        if sheet_name not in wb.sheetnames:
            print(f"[WRITE-BACK] ERROR: Sheet '{sheet_name}' not found!", file=sys.stderr, flush=True)
            return jsonify({"error": f"工作表 '{sheet_name}' 不存在"}), 404
        
        ws = wb[sheet_name]
        
        # 关键：只清空并重写数据行，保留跳过行和表头行不变
        # data_start_row 是数据开始的行（0-based），Excel 行号 = data_start_row + 1
        data_start_excel = data_start_row + 1
        
        # 找到原始数据的最后一行
        max_row = ws.max_row
        max_col = ws.max_column
        
        print(f"[WRITE-BACK] Clearing data rows {data_start_excel}-{max_row}, cols 1-{max_col}", file=sys.stderr, flush=True)
        
        # 只清空数据区域（从 data_start_excel 到最后）
        for row_idx in range(data_start_excel, max_row + 1):
            for col_idx in range(1, max_col + 1):
                ws.cell(row=row_idx, column=col_idx).value = None
        
        # 写入数据（只写纯数据行，从 data_start_excel 开始）
        print(f"[WRITE-BACK] Writing {len(data_df)} data rows starting at Excel row {data_start_excel}", file=sys.stderr, flush=True)
        
        for row_idx, (_, row_data) in enumerate(data_df.iterrows()):
            excel_row = data_start_excel + row_idx
            for col_idx, value in enumerate(row_data, 1):
                ws.cell(row=excel_row, column=col_idx, value=value)
        
        # 保存 uploads 副本
        print(f"[WRITE-BACK] Saving to uploads: {path}...", file=sys.stderr, flush=True)
        import os
        old_size = os.path.getsize(path)
        print(f"[WRITE-BACK] Old size: {old_size} bytes", file=sys.stderr, flush=True)
        
        wb.save(path)
        wb.close()
        
        new_size = os.path.getsize(path)
        print(f"[WRITE-BACK] New size: {new_size} bytes", file=sys.stderr, flush=True)
        
        # 同时写回 assets 原始文件
        if original_filename:
            assets_dir = BASE_DIR / "assets"
            original_path = assets_dir / original_filename
            
            if original_path.exists():
                print(f"[WRITE-BACK] Also writing to original: {original_path}", file=sys.stderr, flush=True)
                # 重新加载刚保存的 workbook（因为 wb 已关闭）
                wb2 = openpyxl.load_workbook(path)
                wb2.save(original_path)
                wb2.close()
                orig_size = os.path.getsize(original_path)
                print(f"[WRITE-BACK] Original file size: {orig_size} bytes", file=sys.stderr, flush=True)
            else:
                print(f"[WRITE-BACK] WARNING: Original file not found at {original_path}", file=sys.stderr, flush=True)
        
        print(f"[WRITE-BACK] DONE!", file=sys.stderr, flush=True)
        
        return jsonify({"message": "已成功写回源文件"})
    
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"[WRITE-BACK] ERROR: {error_detail}", file=sys.stderr, flush=True)
        return jsonify({"error": f"写回失败: {str(e)}"}), 500


@app.route("/api/write-back-email", methods=["POST"])
def api_write_back_email():
    """将修正后的邮箱数据写回源 Excel 文件（同时更新 uploads 副本和 assets 原始文件）。"""
    try:
        import openpyxl
        
        data = request.get_json() or {}
        file_id = data.get("file_id")
        sheet_name = data.get("sheet_name")
        overrides = data.get("overrides", {})
        deleted_rows = data.get("deleted_rows", [])
        email_cfg = data.get("email_config", {})
        original_filename = data.get("original_filename")

        if not file_id or not sheet_name:
            return jsonify({"error": "缺少必要参数"}), 400

        path = UPLOAD_DIR / file_id
        if not path.exists():
            return jsonify({"error": "源文件不存在"}), 404

        # 读取原始 DataFrame
        df = read_excel_raw(path, sheet_name)
        
        # 读取配置
        header_rows_str = email_cfg.get("header_rows", "2")
        skip_rows_str = email_cfg.get("skip_rows", "")
        data_start_str = email_cfg.get("data_start", "3")
        
        header_rows = [int(r) - 1 for r in header_rows_str.split(",") if r.strip()]
        skip_rows = [int(r) - 1 for r in skip_rows_str.split(",") if r.strip()] if skip_rows_str else []
        data_start_row = int(data_start_str) - 1
        
        # 应用删除
        indices_to_delete = set()
        for row_num in deleted_rows:
            df_idx = row_num - 1
            if data_start_row <= df_idx < len(df):
                indices_to_delete.add(df_idx)
        
        # 应用修改（邮箱表的overrides格式：{row_num: {community, name, email}}）
        modified_count = 0
        for row_num_str, fields in overrides.items():
            row_num = int(row_num_str)
            df_idx = row_num - 1
            if data_start_row <= df_idx < len(df) and df_idx not in indices_to_delete:
                headers = fill_merged_headers(df, header_rows)
                for field_name, new_value in fields.items():
                    for col_idx, col_name in enumerate(headers):
                        if col_name == field_name:
                            df.iat[df_idx, col_idx] = new_value
                            modified_count += 1
                            break
        
        # 删除行
        if indices_to_delete:
            sorted_indices = sorted(indices_to_delete, reverse=True)
            for idx in sorted_indices:
                df = df.drop(index=df.index[idx])
            df = df.reset_index(drop=True)
        
        # 提取纯数据行（跳过行和表头行保持原样不动）
        data_df = df.iloc[data_start_row:].reset_index(drop=True)
        
        # 用 openpyxl 直接操作
        wb = openpyxl.load_workbook(path)
        
        if sheet_name not in wb.sheetnames:
            return jsonify({"error": f"工作表 '{sheet_name}' 不存在"}), 404
        
        ws = wb[sheet_name]
        
        # 只清空并重写数据行，保留跳过行和表头行不变
        data_start_excel = data_start_row + 1
        
        max_row = ws.max_row
        max_col = ws.max_column
        
        # 只清空数据区域（从 data_start_excel 到最后）
        for row_idx in range(data_start_excel, max_row + 1):
            for col_idx in range(1, max_col + 1):
                ws.cell(row=row_idx, column=col_idx).value = None
        
        # 写入数据（只写纯数据行，从 data_start_excel 开始）
        for row_idx, (_, row_data) in enumerate(data_df.iterrows()):
            excel_row = data_start_excel + row_idx
            for col_idx, value in enumerate(row_data, 1):
                ws.cell(row=excel_row, column=col_idx, value=value)
        
        # 保存 uploads 副本
        import os
        wb.save(path)
        wb.close()
        
        # 同时写回 assets 原始文件
        if original_filename:
            assets_dir = BASE_DIR / "assets"
            original_path = assets_dir / original_filename
            
            if original_path.exists():
                wb2 = openpyxl.load_workbook(path)
                wb2.save(original_path)
                wb2.close()
        
        return jsonify({"message": "已成功写回源文件"})
    
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"[WRITE-BACK-EMAIL] ERROR: {error_detail}", file=sys.stderr, flush=True)
        return jsonify({"error": f"写回失败: {str(e)}"}), 500


if not USER_CONFIG_PATH.exists():
    save_user_config(json.loads(json.dumps(DEFAULT_USER_CONFIG)))


# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

def _open_dir(path: Path):
    """使用系统默认方式打开目录。"""
    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", str(path)])
    elif system == "Windows":
        # Windows explorer 即使成功也返回非零退出码，必须用 Popen 且不检查返回值
        # 使用 start 命令确保新窗口在前台显示
        subprocess.Popen(["cmd", "/c", "start", "", str(path)], shell=True)
    else:
        subprocess.Popen(["xdg-open", str(path)])


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
    """将项目目录打包为 zip，支持仅代码 / 带数据库文件两种模式。"""
    data = request.get_json() or {}
    mode = data.get("mode") or "code"

    include_db = mode == "with_db"

    packages_dir = BASE_DIR / "packages"
    packages_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "_with_db" if include_db else ""
    zip_path = packages_dir / f"salary_manager_{timestamp}{suffix}.zip"

    exclude_dirs = {"__pycache__", "logs", ".git", "packages", ".idea", ".aicoding-chat-workspace", "uploads", "venv", ".venv", "assets"}
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

        # Windows 下 explorer 命令可能返回非零退出码（即使成功打开），改用 start_new_session + ignore_errors
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(["explorer", str(packages_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Darwin":
            subprocess.run(["open", str(packages_dir)], check=False)
        else:
            subprocess.run(["xdg-open", str(packages_dir)], check=False)

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