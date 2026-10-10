"""依赖下限组合冒烟测试：用 pandas/flask/openpyxl 的最老允许版本跑通解析主链路。

对应目标环境：银河麒麟 V10 SP1（Python 3.8，pip 解析到 pandas 2.0.3 + numpy 1.24.x）。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import flask
import numpy

print(f"[env] python={sys.version.split()[0]} pandas={pd.__version__} "
      f"flask={flask.__version__} numpy={numpy.__version__}")

import app  # 触发模块级：DB 初始化 + Fernet 密钥加载

salary = Path("assets/工资表_2025_04_large.xlsx")
email = Path("assets/邮箱表_2025_04_large.xlsx")

results = []
for path in (salary, email):
    is_email = "邮箱" in path.name
    df = app.read_excel_raw(path, 0)
    header_row = app.detect_header_row(df)
    header_rows = [header_row]
    headers = app.merge_headers(df, header_rows)
    filled_headers = app.fill_merged_headers(df, header_rows)
    dates = app.extract_salary_date(df)
    preview = app.build_preview_rows(df, detected_header=filled_headers, header_rows=header_rows)
    mapping = app.suggest_column_mapping(filled_headers, {"name": ["姓名"], "community": ["社区"]})
    records = app.parse_records(df, header_rows, header_row + 1, df.shape[0] - 1, mapping, is_email=is_email)
    fields = app.detect_salary_fields(filled_headers) if not is_email else {}
    results.append({
        "file": path.name,
        "shape": list(df.shape),
        "header_row": header_row,
        "headers_head": filled_headers[:5],
        "dates": str(dates),
        "mapping": mapping,
        "records": len(records[0]) if isinstance(records, tuple) else len(records),
        "preview_type": type(preview).__name__,
        "salary_fields": fields,
    })

# 单元格级读写 API（pandas 2.0 行为变更高发区：iat 写入 / drop / reset_index / to_excel）
df = app.read_excel_raw(salary, 0)
df = df.astype(object)
df.iat[df.shape[0] - 1, 0] = "冒烟测试"
df = df.drop(index=df.index[1]).reset_index(drop=True)
out = Path(tempfile.gettempdir()) / "smoke_export.xlsx"
df.to_excel(out, index=False, engine="openpyxl")
back = pd.read_excel(out, engine="openpyxl")

# 邮箱清洗 / 校验 / 数值转换
cleaned = app.clean_email("  张三 <Zhang.San@QQ.com> ")
ok = app.validate_email(cleaned)
f2 = app.to_float("12,345.67")

print("[parse]")
for r in results:
    print("  ", r)
print(f"[write] drop/reset/iat/to_excel OK: {back.shape}, 文件={out.name}")
print(f"[email] clean={cleaned!r} validate={ok} to_float={f2}")
print(f"[crypto] roundtrip={app.decrypt_password(app.encrypt_password('冒烟-授权码'))!r}")
print("SMOKE OK")
