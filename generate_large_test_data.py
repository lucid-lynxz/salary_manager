#!/usr/bin/env python3
"""生成 200 条左右的工资表 + 邮箱表测试数据，用于长表格效果测试。

生成的文件结构与系统自动识别规则兼容：
- 工资表：第 1 行标题（含年月），第 3-4 行表头，第 5 行起数据
- 邮箱表：第 1 行标题（含年月），第 2 行表头，第 3 行起数据

用法：
    python generate_large_test_data.py                    # 默认生成 2025 年 4 月、200 人
    python generate_large_test_data.py 2025 6 250         # 指定 2025 年 6 月、250 人
"""
import os
import random
import sys
import datetime
from openpyxl import Workbook

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# 社区列表
COMMUNITIES = ["东山", "南山", "西山", "北山", "中山", "上河", "下河", "前湾", "后湾", "新园"]

# 姓氏
SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")

# 名字用字
GIVEN_CHARS = list("伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英文华建国建华建军晓东晓红晓燕晓敏晓明志强志华志军志勇志明建军建华建明建强国华国庆国栋国强国军")

# 岗位
POSITIONS = ["社工", "管理员", "出纳", "会计", "司机", "保洁", "保安", "文员", "主管", "副主任", "主任", "书记"]

# 邮箱域名
DOMAINS = ["qq.com", "163.com", "126.com", "gmail.com", "outlook.com", "foxmail.com"]

# 拼音姓氏（用于生成 ASCII 邮箱用户名）
PINYIN_SURNAMES = ["zhao", "qian", "sun", "li", "zhou", "wu", "zheng", "wang",
                   "feng", "chen", "chu", "wei", "jiang", "shen", "han", "yang",
                   "zhu", "qin", "you", "xu", "he", "lv", "shi", "zhang",
                   "kong", "cao", "yan", "hua", "jin", "wei", "tao", "jiang"]
PINYIN_GIVEN = ["wei", "fang", "na", "xiu", "ying", "min", "jing", "li", "qiang",
                "lei", "jun", "yang", "yong", "yan", "jie", "juan", "tao", "ming",
                "chao", "lan", "xia", "ping", "gang", "gui", "wen", "hua", "jian",
                "guo", "dong", "hong", "yan", "zhi", "qiang", "hua", "jun", "ming"]


def random_pinyin_name():
    return random.choice(PINYIN_SURNAMES) + random.choice(PINYIN_GIVEN)


def random_name():
    surname = random.choice(SURNAMES)
    given_len = random.choice([1, 1, 2, 2])  # 偏向双字名
    given = "".join(random.choice(GIVEN_CHARS) for _ in range(given_len))
    return surname + given


def random_bank_account():
    # 19 位银行卡号，以 6228 开头
    return "6228" + "".join(random.choice("0123456789") for _ in range(15))


def random_email(name, idx):
    # 全部生成有效邮箱：拼音用户名 + 序号 + 域名
    pinyin = random_pinyin_name()
    domain = random.choice(DOMAINS)
    return f"{pinyin}{idx}@{domain}"


def generate_salary(year, month, count, output_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "工资表"

    # 第 1 行：标题（含年月，供系统自动识别月份）
    ws.cell(row=1, column=1, value=f"{year}年{month}月 单位:元")
    # 第 2 行：空
    # 第 3 行：一级表头（部分列留空，由 fill_merged_headers 取最下方非空值）
    headers_top = ["", "", "", "", "工资收入", "", "", "应发工资", "代扣项目", "", "", "", "代扣合计", "实发工资", "银行卡号"]
    # 第 4 行：二级表头（实际列名）
    headers = ["序号", "社区", "姓名", "岗位", "基本工资", "岗位津贴", "绩效工资",
               "应发工资", "养老保险", "医疗保险", "失业保险", "住房公积金",
               "代扣合计", "实发工资", "银行卡号"]
    for col, val in enumerate(headers_top, start=1):
        ws.cell(row=3, column=col, value=val)
    for col, val in enumerate(headers, start=1):
        ws.cell(row=4, column=col, value=val)

    # 第 5 行起：数据
    for i in range(1, count + 1):
        row = 4 + i
        community = random.choice(COMMUNITIES)
        name = random_name()
        position = random.choice(POSITIONS)
        base = random.randint(3000, 8000)
        allowance = random.randint(500, 2000)
        performance = random.randint(1000, 5000)
        gross = base + allowance + performance
        pension = round(gross * 0.08, 2)
        medical = round(gross * 0.02, 2)
        unemployment = round(gross * 0.005, 2)
        housing = round(gross * 0.12, 2)
        deduction = round(pension + medical + unemployment + housing, 2)
        net = round(gross - deduction, 2)
        bank = random_bank_account()

        values = [i, community, name, position, base, allowance, performance,
                  gross, pension, medical, unemployment, housing,
                  deduction, net, bank]
        for col, val in enumerate(values, start=1):
            ws.cell(row=row, column=col, value=val)

    # 调整列宽
    widths = [6, 10, 10, 10, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 22]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=4, column=idx).column_letter].width = w

    wb.save(output_path)
    print(f"生成工资表（{count} 人）：{output_path}")


def generate_email(year, month, people, output_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "邮箱表"

    # 第 1 行：标题
    ws.cell(row=1, column=1, value=f"{year}年{month}月 人员邮箱")
    # 第 2 行：表头
    headers = ["序号", "社区", "姓名", "邮箱"]
    for col, val in enumerate(headers, start=1):
        ws.cell(row=2, column=col, value=val)

    for i, (idx, community, name, email) in enumerate(people, start=1):
        row = 2 + i
        ws.cell(row=row, column=1, value=i)
        ws.cell(row=row, column=2, value=community)
        ws.cell(row=row, column=3, value=name)
        ws.cell(row=row, column=4, value=email)

    widths = [6, 10, 10, 30]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=2, column=idx).column_letter].width = w

    wb.save(output_path)
    print(f"生成邮箱表（{len(people)} 人）：{output_path}")


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    month = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    random.seed(42)

    # 先生成人员清单（保证工资表与邮箱表的人员一一对应）
    people = []
    for i in range(1, count + 1):
        community = random.choice(COMMUNITIES)
        name = random_name()
        email = random_email(name, i)
        people.append((i, community, name, email))

    suffix = f"{year}_{month:02d}_large.xlsx"
    salary_path = os.path.join(ASSETS_DIR, f"工资表_{suffix}")
    email_path = os.path.join(ASSETS_DIR, f"邮箱表_{suffix}")

    generate_salary(year, month, count, salary_path)
    generate_email(year, month, people, email_path)
    print(f"\n测试数据生成完成（{count} 人，{year}年{month}月）。")
    print(f"工资表：{salary_path}")
    print(f"邮箱表：{email_path}")


if __name__ == "__main__":
    main()
