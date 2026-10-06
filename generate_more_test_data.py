#!/usr/bin/env python3
"""基于已有的 工资表_YYYY_MM_large.xlsx 人员名单与列名，批量生成更多月份的测试数据。

与 generate_large_test_data.py 的区别：人员清单不重新随机生成，而是从基准文件读取，
保证跨月份的人员（社区 + 姓名 + 邮箱 + 银行卡号）一致，工资按月小幅浮动，
这样才能测出"按月份追溯历史工资"的效果。

用法：
    python generate_more_test_data.py                       # 用默认月份（2025 年 1-3、5-6 月）
    python generate_more_test_data.py --base 2025_04        # 指定基准文件月份
    python generate_more_test_data.py 2025:7 2025:8 2025:9  # 指定要生成的月份
"""
import argparse
import datetime
import os
import random
import re
import sys
from collections import deque

from openpyxl import Workbook, load_workbook

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

HEADER_TOP_ROW = 3
HEADER_ROW = 4
FIRST_DATA_ROW = 5
DATA_SHEET_TITLE = "工资表"
EMAIL_SHEET_TITLE = "邮箱表"


def read_people(year, month):
    """从基准工资表 + 邮箱表读出人员清单和列名。

    返回 (columns, top_headers, people)：
      columns      -- 二级表头（真实列名），新文件沿用同一套列
      top_headers  -- 一级表头（含"工资收入""代扣项目"等分组标题）
      people       -- 逐行的人员信息，邮箱按 社区+姓名 从邮箱表匹配而来
    """
    salary_path = os.path.join(ASSETS_DIR, f"工资表_{year}_{month:02d}_large.xlsx")
    email_path = os.path.join(ASSETS_DIR, f"邮箱表_{year}_{month:02d}_large.xlsx")
    for path in (salary_path, email_path):
        if not os.path.exists(path):
            sys.exit(f"基准文件不存在：{path}\n请先运行 generate_large_test_data.py 生成 {year}年{month}月 的数据。")

    salary_wb = load_workbook(salary_path, read_only=True, data_only=True)
    ws = salary_wb.worksheets[0]
    header_rows = list(ws.iter_rows(min_row=HEADER_TOP_ROW, max_row=HEADER_ROW, values_only=True))
    top_headers, columns = header_rows[0], header_rows[-1]
    if len(columns) != len(top_headers):
        sys.exit(f"一级表头({len(top_headers)}列)与二级表头({len(columns)}列)列数不一致")

    col_idx = {name: i for i, name in enumerate(columns)}
    required = ["社区", "姓名"]
    missing = [name for name in required if name not in col_idx]
    if missing:
        sys.exit(f"基准工资表缺少列：{missing}，实际列为 {columns}")

    # 邮箱表按 社区+姓名 建立索引（同名同人可能有多个邮箱记录，用队列按序消费）。
    # 两份文件的行序可能不一致（邮箱表被单独修改过），所以不能按序号配对。
    email_ws = load_workbook(email_path, read_only=True, data_only=True).worksheets[0]
    email_header = list(email_ws.iter_rows(min_row=2, max_row=2, values_only=True))[0]
    email_idx = {name: i for i, name in enumerate(email_header) if name}
    if not {"社区", "姓名", "邮箱"} <= set(email_idx):
        sys.exit(f"基准邮箱表缺少必需列，实际表头为 {email_header}")

    email_map = {}
    for record in email_ws.iter_rows(min_row=3, values_only=True):
        key = (record[email_idx["社区"]], record[email_idx["姓名"]])
        if not all(key):
            continue
        email_map.setdefault(key, deque()).append(record[email_idx["邮箱"]])

    people = []
    unmatched = 0
    for record in ws.iter_rows(min_row=FIRST_DATA_ROW, values_only=True):
        community, name = record[col_idx["社区"]], record[col_idx["姓名"]]
        if not community or not name:
            continue
        queue = email_map.get((community, name))
        email = queue.popleft() if queue else None
        if email is None:
            unmatched += 1
        person = {"社区": community, "姓名": name, "邮箱": email}
        for extra in ("岗位", "银行卡号"):
            if extra in col_idx:
                person[extra] = record[col_idx[extra]]
        # 工资基数：用于按月浮动
        person["_gross"] = record[col_idx["应发工资"]] if "应发工资" in col_idx else None
        people.append(person)

    if unmatched:
        print(f"提示：{unmatched} 人在邮箱表中没有对应记录，生成的邮箱表中该行邮箱留空（可测 missing 场景）。")

    if not people:
        sys.exit("基准工资表没读到有效人员行。")
    return columns, top_headers, people


def build_month(people, columns, year, month, rng):
    """给一个月度快照生成工资数值；每个人员有自己的浮动系数，跨月平滑变化。"""
    col = {name: i for i, name in enumerate(columns)}
    rows = []
    for i, person in enumerate(people, start=1):
        base_gross = person["_gross"]
        if not isinstance(base_gross, (int, float)):
            base_gross = rng.randint(5000, 12000)
        # 以 4 月工资为锚点，按月份序号和人员做小幅漂移，再叠一点随机噪声
        drift = 1 + 0.004 * (month - 4) + rng.uniform(-0.02, 0.02)
        gross = round(base_gross * drift, 2)
        housing = round(gross * 0.12, 2)
        pension = round(gross * 0.08, 2)
        medical = round(gross * 0.02, 2)
        unemployment = round(gross * 0.005, 2)
        deduction = round(pension + medical + unemployment + housing, 2)
        net = round(gross - deduction, 2)

        # 收入拆分：基本工资为主，津贴/绩效分摊剩余部分
        base_pay = round(gross * rng.uniform(0.5, 0.65), 2)
        allowance = round(gross * rng.uniform(0.12, 0.22), 2)
        performance = round(gross - base_pay - allowance, 2)

        values = [None] * len(columns)

        def put(name, value):
            if name in col:
                values[col[name]] = value

        put("序号", i)
        put("社区", person["社区"])
        put("姓名", person["姓名"])
        put("岗位", person.get("岗位"))
        put("基本工资", base_pay)
        put("岗位津贴", allowance)
        put("绩效工资", performance)
        put("应发工资", gross)
        put("养老保险", pension)
        put("医疗保险", medical)
        put("失业保险", unemployment)
        put("住房公积金", housing)
        put("代扣合计", deduction)
        put("实发工资", net)
        put("银行卡号", person.get("银行卡号"))
        rows.append(values)
    return rows


def write_salary(year, month, columns, top_headers, rows, path):
    wb = Workbook()
    ws = wb.active
    ws.title = DATA_SHEET_TITLE
    # 第 1 行标题带年月，供系统自动识别月份；第 2 行留空；3/4 行双层表头
    ws.cell(row=1, column=1, value=f"{year}年{month}月 单位:元")
    for i, val in enumerate(top_headers, start=1):
        if val is not None:
            ws.cell(row=HEADER_TOP_ROW, column=i, value=val)
    for i, val in enumerate(columns, start=1):
        ws.cell(row=HEADER_ROW, column=i, value=val)
    for r, values in enumerate(rows):
        for c, val in enumerate(values, start=1):
            if val is not None:
                ws.cell(row=FIRST_DATA_ROW + r, column=c, value=val)
    for i, name in enumerate(columns, start=1):
        width = 22 if name == "银行卡号" else max(8, len(str(name)) * 2 + 4)
        ws.column_dimensions[ws.cell(row=HEADER_ROW, column=i).column_letter].width = width
    wb.save(path)


def write_email(year, month, people, path):
    """邮箱表按基准人员的顺序生成，保证与工资表的社区/姓名逐行对应。"""
    wb = Workbook()
    ws = wb.active
    ws.title = EMAIL_SHEET_TITLE
    ws.cell(row=1, column=1, value=f"{year}年{month}月 人员邮箱")
    headers = ["序号", "社区", "姓名", "邮箱"]
    for i, val in enumerate(headers, start=1):
        ws.cell(row=2, column=i, value=val)
    for r, person in enumerate(people, start=1):
        ws.cell(row=2 + r, column=1, value=r)
        ws.cell(row=2 + r, column=2, value=person["社区"])
        ws.cell(row=2 + r, column=3, value=person["姓名"])
        ws.cell(row=2 + r, column=4, value=person["邮箱"])
    for i, width in enumerate([6, 10, 10, 30], start=1):
        ws.column_dimensions[ws.cell(row=2, column=i).column_letter].width = width
    wb.save(path)


def parse_month_arg(text):
    m = re.fullmatch(r"(\d{4})[:-_.](\d{1,2})", text)
    if not m:
        raise argparse.ArgumentTypeError(f"月份格式应为 2025:7，收到 {text!r}")
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise argparse.ArgumentTypeError(f"月份超出范围：{text!r}")
    datetime.date(year, month, 1)
    return year, month


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("months", nargs="*", type=parse_month_arg,
                        help="要生成的月份，形如 2025:7；留空使用默认（2025 年 1-3 月和 5-6 月）")
    parser.add_argument("--base", default="2025:4", type=parse_month_arg,
                        help="人员名单与列名的基准文件月份，默认 2025:4")
    parser.add_argument("--seed", type=int, default=None,
                        help="随机种子，固定可复现")
    args = parser.parse_args()

    targets = args.months or [(2025, 1), (2025, 2), (2025, 3), (2025, 5), (2025, 6)]
    base_year, base_month = args.base
    columns, top_headers, people = read_people(base_year, base_month)
    print(f"基准 {base_year}年{base_month}月：{len(people)} 人，列名 {columns}")

    os.makedirs(ASSETS_DIR, exist_ok=True)
    made = []
    for year, month in targets:
        if (year, month) == (base_year, base_month):
            print(f"跳过 {year}年{month}月（基准文件已存在）")
            continue
        # 不同月份用不同种子，保证各月工资不同；给了 --seed 则可复现
        month_seed = args.seed if args.seed is not None else (args.base[0] * 100 + args.base[1]) * 100 + year * 12 + month
        rows = build_month(people, columns, year, month, random.Random(month_seed))
        suffix = f"{year}_{month:02d}_large.xlsx"
        salary_path = os.path.join(ASSETS_DIR, f"工资表_{suffix}")
        email_path = os.path.join(ASSETS_DIR, f"邮箱表_{suffix}")
        write_salary(year, month, columns, top_headers, rows, salary_path)
        write_email(year, month, people, email_path)
        made.append((year, month, len(rows)))
        print(f"生成 {year}年{month}月（{len(rows)} 人）：\n  {salary_path}\n  {email_path}")

    print(f"\n完成，共生成 {len(made)} 个月。")
    print("注意：邮箱沿用基准文件，未匹配/异常邮箱场景请用 工资表_异常样例_2025_09.xlsx 测试。")


if __name__ == "__main__":
    main()
