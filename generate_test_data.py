#!/usr/bin/env python3
"""基于现有模板生成 3 个月的工资表+邮箱表测试数据。"""
import datetime
import random
import os
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

SALARY_TEMPLATE = os.path.join(ASSETS_DIR, "副本在职2025.01（需打印两份，报销单复印一份）.xlsx")
EMAIL_TEMPLATE = os.path.join(ASSETS_DIR, "人员邮箱.xlsx")

MONTHS = [
    (2025, 1),
    (2025, 2),
    (2025, 3),
]


def variant_salary_value(value, month, col_letter):
    """对数值型工资项按月份做微小变动，保持合理性。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # 第 1 列序号保持整数
        if col_letter == "A":
            return int(value)
        # 固定工资类列保持相对稳定，其它按月份微调
        factor = 1 + (month - 1) * random.uniform(-0.03, 0.05)
        return round(value * factor, 2)
    return value


def datediff_years_months(start, end):
    """模拟 Excel DATEDIF(start, end+1, ...) 计算居龄字符串。"""
    # 使用 end + 1 天
    end = end + datetime.timedelta(days=1)
    years = end.year - start.year
    months = end.month - start.month
    if end.day < start.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    return f"{years}年{months}个月"


def generate_salary(year, month, output_path):
    wb = load_workbook(SALARY_TEMPLATE)
    ws = wb.active
    # 修改标题日期行（A2）
    ws["A2"] = f"{year}年{month}月 单位:元"

    # 数据行从第 5 行开始（表头 3、4 行）
    data_start_row = 5
    stat_date = datetime.datetime(year, month, 1)

    for row_idx in range(data_start_row, ws.max_row + 1):
        # 只处理真正的数据行：A 列为整数序号；跳过合计/审核等合并行
        first_cell = ws.cell(row=row_idx, column=1)
        if isinstance(first_cell, MergedCell) or not isinstance(first_cell.value, int):
            continue

        for col_idx in range(1, ws.max_column + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            if isinstance(cell, MergedCell):
                continue
            col_letter = get_column_letter(col_idx)
            cell.value = variant_salary_value(cell.value, month, col_letter)

        # 更新统计时点为该月 1 日
        ws.cell(row=row_idx, column=5).value = stat_date

        # 重新计算居龄、应发工资、代扣合计、实发工资，避免公式丢失缓存值导致 pandas 读空
        start_date_cell = ws.cell(row=row_idx, column=4)
        service_years_cell = ws.cell(row=row_idx, column=6)
        if isinstance(start_date_cell.value, datetime.datetime):
            service_years_cell.value = datediff_years_months(start_date_cell.value, stat_date)

        def get_num(col):
            v = ws.cell(row=row_idx, column=col).value
            return v if isinstance(v, (int, float)) else 0

        gross = sum(get_num(c) for c in range(9, 17)) - get_num(17)
        total_deduction = sum(get_num(c) for c in range(19, 24))
        net = gross - total_deduction

        ws.cell(row=row_idx, column=18).value = round(gross, 2)
        ws.cell(row=row_idx, column=24).value = round(total_deduction, 2)
        ws.cell(row=row_idx, column=25).value = round(net, 2)

    wb.save(output_path)
    print(f"生成工资表：{output_path}")


def generate_email(year, month, output_path):
    wb = load_workbook(EMAIL_TEMPLATE)
    ws = wb.active
    # 邮箱表保持人员和邮箱不变，仅把标题日期（A2）改成对应月份
    ws["A2"] = f"{year}年{month}月"
    wb.save(output_path)
    print(f"生成邮箱表：{output_path}")


def main():
    random.seed(42)
    for year, month in MONTHS:
        suffix = f"{year}_{month:02d}.xlsx"
        salary_path = os.path.join(ASSETS_DIR, f"工资表_{suffix}")
        email_path = os.path.join(ASSETS_DIR, f"邮箱表_{suffix}")
        generate_salary(year, month, salary_path)
        generate_email(year, month, email_path)
    print("测试数据生成完成。")


if __name__ == "__main__":
    main()
