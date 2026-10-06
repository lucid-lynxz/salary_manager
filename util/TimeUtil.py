# !/usr/bin/env python3
# -*- coding:utf-8 -*-

import functools
import inspect
import random
import time
from datetime import datetime, timedelta


def log_time_consume(exclude_params=None, separate: bool = False, only_log_result: bool = True):
    """
    记录函数执行时间和参数的装饰器

    参数:
    :param exclude_params: 需要排除的参数名列表（默认包含 'self' 和 'cls'）
    :param separate: 是否跳行打印参数, 表示会打印第一个语句之前加一个空行, 并在最后一个语句之后加一个空行
    :param only_log_result: 是否只在调用完成后才打印耗时等信息, 默认True, 若为False,则会在调用前打印参数
    示例:
    @log_time_consume(exclude_params=['password'])
    def login(username, password):
        pass
    """
    # 设置默认排除参数
    default_exclude = ['self', 'cls']
    if exclude_params is None:
        exclude_params = default_exclude
    else:
        # 确保默认参数在列表中
        exclude_params = list(set(exclude_params + default_exclude))

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 获取函数签名以获取参数名
            sig = inspect.signature(func)
            bound_args = sig.bind(*args, **kwargs)
            bound_args.apply_defaults()

            # 准备打印的参数
            params_to_print = {}
            for name, value in bound_args.arguments.items():
                if name not in exclude_params:
                    params_to_print[name] = value

            # 打印参数
            blank_line = '\n' if separate else ''
            if not only_log_result:
                if params_to_print:
                    print(f'{TimeUtil.getTimeStr()} {blank_line}执行 {func.__name__} 调用,参数: {params_to_print}')
                else:
                    print(f'{TimeUtil.getTimeStr()} {blank_line}执行 {func.__name__} 调用,无参数')

            # 计时
            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()

            # 打印耗时
            time_tip = f'执行耗时: {end_time - start_time:.2f} 秒'
            if only_log_result:
                if params_to_print:
                    print(
                        f'{TimeUtil.getTimeStr()} {blank_line}执行 {func.__name__} 调用,{time_tip},参数: {params_to_print}')
                else:
                    print(f'{TimeUtil.getTimeStr()} {blank_line}执行 {func.__name__} 调用,{time_tip},无参数')
            else:
                print(f'{TimeUtil.getTimeStr()} {func.__name__} {time_tip}{blank_line}')

            return result

        return wrapper

    return decorator


class TimeUtil(object):
    @staticmethod
    def getTimeStr(fmt="%Y-%m-%d %H:%M:%S", n: int = 0) -> str:
        """
        获取N天前的时间,并按给定的格式返回字符串结果
        :param fmt: 日期格式,  %Y 代表四位数的年份，%m 代表两位数的月份，%d 代表两位数的日期
        :param n: 天数, 0表示当天, 正数表示N天前, 负数表示N天后
        """
        current_date = datetime.now()  # 获取当前日期
        n_days_ago = current_date - timedelta(days=n)  # 计算 N 天前的日期
        return n_days_ago.strftime(fmt)  # 将日期转换为指定格式的字符串
        # return time.strftime(format, time.localtime(time.time()))

    @staticmethod
    def getTimeObj(fmt="%Y-%m-%d %H:%M:%S", n: int = 0, target_date: str = None) -> datetime:
        """
        获取N天前的时间,并按给定的格式返回 datetime 对象
        :param fmt: 日期格式,  %Y 代表四位数的年份，%m 代表两位数的月份，%d 代表两位数的日期
        :param n: 天数, 0表示当天, 正数表示N天前, 负数表示N天后
        :param target_date: 目标日期, 默认为None, 表示使用当前日期 n 参数计算得到的日期
        """
        target_date = target_date if target_date else TimeUtil.getTimeStr(fmt, n)
        return datetime.strptime(target_date, fmt)

    @staticmethod
    def convertFormat(dateStr: str, oldFormat: str, newFormat: str = '%Y-%m-%d') -> str:
        """
        转换日期格式
        """
        # print('--> convertFormat(%s,%s,%s)' % (dateStr, oldFormat, newFormat))
        try:
            result = datetime.strptime(dateStr, oldFormat).strftime(newFormat)
        except Exception as e:
            result = dateStr
        return result

    @staticmethod
    def convertSecsDuration(totalSecs: float) -> str:
        """
        将所给的秒数转换为易读的字符串, 格式: x小时y分z秒
        """
        totalSecs = int(totalSecs)
        hour = int(totalSecs // 3600)
        rest = totalSecs % 3600
        minutes = int(rest // 60)
        secs = int(rest % 60)
        result = '%s小时%s分%s秒' % (hour, minutes, secs)
        return result.replace('分0秒', '分').replace('小时0分', '小时').replace('0小时', '')

    @staticmethod
    def currentTimeMillis() -> int:
        """获取当前时间戳,单位:ms"""
        return int(round(time.time() * 1000))

    @staticmethod
    def convertTimestamp(timestamp: int, fmt: str = '%Y-%m-%d %H:%M:%S') -> str:
        """
        将毫秒级时间戳转换为时间字符串
        :param timestamp: 毫秒级时间戳
        :param fmt: 日期格式, 默认为: '%Y-%m-%d %H:%M:%S'
        :return: 格式化后的时间字符串
        """
        # 将毫秒转换为秒
        seconds = timestamp / 1000
        dt = datetime.fromtimestamp(seconds)
        return dt.strftime(fmt)

    @staticmethod
    def getDurationStr(durationInMills: int) -> str:
        """将毫秒耗时转换更可读的 x时x分x秒的形式"""
        mills = durationInMills % 1000
        restSeconds = durationInMills // 1000
        seconds = restSeconds % 60
        restMinutes = restSeconds // 60
        minutes = restMinutes % 60
        hours = restSeconds // 3600
        result = f'{hours}小时{minutes}分{seconds}秒'
        if result.startswith('0小时'):
            result = result.replace('0小时', '')
        if result.startswith('0分') or '时0分' in result:
            result = result.replace('0分', '')
        if result.endswith('分0秒'):
            result = result.replace('0秒', '')
        return result

    @staticmethod
    def sleep(sec: float, minSec: float = 1, maxSec: float = 10, print_log: bool = False) -> float:
        """
        等待一会
        :param sec: 等待指定的秒数，大于0有效，若小于0，则会在 [minSec,maxSec) 中随机算一个
        :return 最终使用的时长,单位:s
        """
        if sec == 0:
            return 0

        if sec < 0:
            sec = round(minSec + random.random() * (maxSec - minSec), 1)  # 保留一位小数

        if sec < 0:
            sec = 1

        if sec > 0:
            if print_log:
                print(f'sleep {sec}s')
            time.sleep(sec)
        return sec

    @staticmethod
    def calc_sec_diff(time1: str, time2: str, fmt: str = '%Y-%m-%d', def_value: int = 0) -> float:
        """
        比较两个日期相差的天数
        :param time1: 第一个日期字符串,如: 2022-06-27 也支持时间或者日期+时间格式,具体根据 dateFormat 来决定
        :param time2: 第二个日期字符串,如: 2022-06-28
        :param fmt: 日期格式, 默认为: '%Y-%m-%d' 也可用是: '%Y-%m-%d %H:%M:%S' 等
        :param def_value: 运算出错时返回的值, 默认为0
        :return time1 与 time2 相差的秒数, 负数表示 time1 早于 time2 ,  如: -1
        """
        try:
            # 将字符串解析为datetime对象
            time1 = datetime.strptime(time1, fmt)
            time2 = datetime.strptime(time2, fmt)
            time_diff = time1 - time2  # 计算时间差
            # 获取相差的秒数
            diff = time_diff.total_seconds()
            return diff
        except Exception as e:
            print(f'calc_sec_diff("{time1}","{time2}", "{fmt}", {def_value}): exception {e}')
            return def_value

    @staticmethod
    def dateDiff(date1: str, date2: str, dateFormat: str = '%Y-%m-%d', valueOnError: int = 0) -> int:
        """
        比较两个日期相差的天数
        :param date1: 第一个日期字符串,如: 2022-06-27 也支持时间或者日期+时间格式,具体根据 dateFormat 来决定
        :param date2: 第二个日期字符串,如: 2022-06-28
        :param dateFormat: 日期格式, 默认为: '%Y-%m-%d' 也可用是: '%Y-%m-%d %H:%M:%S' 等
        :param valueOnError: 运算出错时返回的值, 默认为0
        :return date1与date2相差的天数, 负数表示date1早于date2,  如: -1
        """
        diff_sec = TimeUtil.calc_sec_diff(date1, date2, dateFormat, valueOnError)
        return int(diff_sec / 24 / 60 / 60)

    @staticmethod
    def is_time_greater_than(target_str: str, include_equal: bool = False) -> bool:
        """
        比较当前时间是否大于等于目标时间字符串
        :param target_str: 目标时间字符串, 支持格式: "YYYY-MM-DD", "HH:MM:SS", "YYYY-MM-DD HH:MM:SS"
        :param include_equal: 是否包含等于的情况, 默认为False, 即当前时间大于目标时间时才返回True
        """
        target_str = target_str.strip().replace("  ", " ")
        time_format = TimeUtil.get_time_format(target_str)
        try:
            if time_format is None:
                raise ValueError(f"无法解析时间格式: {target_str}")

            cur_time = TimeUtil.getTimeStr(time_format)
            diff_sec = TimeUtil.calc_sec_diff(cur_time, target_str, time_format)
            # print(f'  cur_time={cur_time}, target={target_str},diff_sec={diff_sec},time_format={time_format}')
            return diff_sec >= 0 if include_equal else diff_sec > 0
        except ValueError as e:
            print(f"时间解析错误: {e}")
            return False

    @staticmethod
    def get_time_format(target_str: str) -> str:
        """
        尝试解析为日期时间格式
        支持格式: "YYYY-MM-DD", "YYYYMMDD", "HH:MM:SS", "YYYY-MM-DD HH:MM:SS", "YYYYMMDD HH:MM:SS"
        """
        time_format = None
        if ' ' in target_str:
            if '-' in target_str:
                time_format = "%Y-%m-%d %H:%M:%S"
            else:
                time_format = "%Y%m%d %H:%M:%S"
        # 尝试解析为日期格式
        elif '-' in target_str:
            time_format = "%Y-%m-%d"
        # 尝试解析为时间格式
        elif ':' in target_str:
            time_format = "%H:%M:%S"
        return time_format

    @staticmethod
    def timestamp_to_str(timestamp: float, fmt: str = '%Y-%m-%d %H:%M:%S.%f') -> str:
        """
        将时间戳转换为指定格式的时间字符串，自动识别秒或毫秒级别

        :param timestamp: 时间戳（秒或毫秒）
                         - 如果值 > 10^12，认为是毫秒级时间戳
                         - 否则认为是秒级时间戳
        :param fmt: 输出格式，默认 '%Y-%m-%d %H:%M:%S.%f'（包含微秒）
                   - '%Y-%m-%d %H:%M:%S'：不包含微秒
                   - '%Y-%m-%d'：仅日期
                   - '%H:%M:%S'：仅时间
        :return: 格式化后的时间字符串

        :example:
        # 示例 1: 秒级时间戳
        ts = 1714032000.123456
        result = timestamp_to_str(ts)
        # 返回: '2024-04-25 12:00:00.123456'

        # 示例 2: 毫秒级时间戳
        ts = 1714032000123
        result = timestamp_to_str(ts)
        # 返回: '2024-04-25 12:00:00.123000'

        # 示例 3: 自定义格式（不含微秒）
        result = timestamp_to_str(ts, fmt='%Y-%m-%d %H:%M:%S')
        # 返回: '2024-04-25 12:00:00'

        # 示例 4: 当前时间戳
        import time
        result = timestamp_to_str(time.time())
        """
        try:
            # 自动判断时间戳单位
            # 毫秒级时间戳通常 > 10^12 (即 2001-09-09 之后的毫秒时间戳)
            if timestamp > 1e12:
                # 毫秒转秒
                timestamp_sec = timestamp / 1000.0
            else:
                # 已经是秒级
                timestamp_sec = timestamp

            # 转换为 datetime 对象
            dt = datetime.fromtimestamp(timestamp_sec)

            # 格式化输出
            return dt.strftime(fmt)
        except Exception as e:
            print(f"❌ 时间戳转换失败: {timestamp}, 错误: {e}")
            return str(timestamp)

    @staticmethod
    def str_to_timestamp_ms(time_str: str, fmt: str = None) -> int:
        """
        将时间字符串转换为毫秒级时间戳

        :param time_str: 时间字符串
                        - 支持格式: 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM:SS.ffffff' 等
                        - 如果未指定 fmt，会自动尝试识别常见格式
        :param fmt: 时间格式，如 '%Y-%m-%d %H:%M:%S'
                   - 如果为 None，则自动识别格式
        :return: 毫秒级时间戳（int）

        :example:
        # 示例 1: 自动识别格式
        ts = str_to_timestamp_ms('2024-04-25 12:00:00')
        # 返回: 1714017600000

        # 示例 2: 指定格式
        ts = str_to_timestamp_ms('2024-04-25', fmt='%Y-%m-%d')
        # 返回: 1714003200000

        # 示例 3: 带微秒的时间
        ts = str_to_timestamp_ms('2024-04-25 12:00:00.123456')
        # 返回: 1714017600123

        # 示例 4: 仅日期（默认时间为 00:00:00）
        ts = str_to_timestamp_ms('2024-04-25')
        # 返回: 1714003200000
        """
        try:
            # 如果未指定格式，尝试自动识别
            if fmt is None:
                fmt = TimeUtil.get_time_format(time_str)
                if fmt is None:
                    # 尝试更多常见格式
                    for try_fmt in ['%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
                        try:
                            datetime.strptime(time_str, try_fmt)
                            fmt = try_fmt
                            break
                        except ValueError:
                            continue

                    if fmt is None:
                        raise ValueError(f"无法识别时间格式: {time_str}")

            # 解析时间字符串为 datetime 对象
            dt = datetime.strptime(time_str, fmt)

            # 转换为时间戳（秒）
            timestamp_sec = dt.timestamp()

            # 转换为毫秒并取整
            timestamp_ms = int(timestamp_sec * 1000)

            return timestamp_ms
        except Exception as e:
            print(f"❌ 时间字符串转换失败: {time_str}, 错误: {e}")
            return 0

    @staticmethod
    def _parse_to_minutes(time_str: str) -> int:
        """
        内部方法：将时间字符串统一转换为当天的分钟数 (0-1440)。
        - 忽略秒数，只精确到分钟。
        - 特殊处理 '24:xx' 为 1440 分钟。

        :param time_str: 时间字符串，如 '07:00', '07:00:30', '24:00'
        :return: 整数分钟数
        """
        if not time_str:
            raise ValueError("时间字符串不能为空")

        time_str = time_str.strip()

        # 特殊处理 24:00 或 24:00:00，视为当天终点 (1440 分钟)
        if time_str.startswith("24:"):
            return 1440

        parts = time_str.split(":")
        if len(parts) < 2:
            raise ValueError(f"时间格式错误，期望 HH:MM 或 HH:MM:SS，收到：{time_str}")

        try:
            hours = int(parts[0])
            minutes = int(parts[1])
            # 秒数部分直接忽略，满足“只判断到分钟级别”的需求
        except ValueError:
            raise ValueError(f"时间数值解析失败：{time_str}")

        return hours * 60 + minutes

    @classmethod
    def is_in_range(cls, target_time: str, time_range: str) -> bool:
        """
        判断目标时间是否落在指定的时间区间内。
        自动处理跨天场景（如 22:00-05:00）和格式不一致场景（如秒级 vs 分钟级）。

        :param target_time: 目标时间点，例：'17:55:38' 或 '17:55'
        :param time_range: 时间区间字符串，例：'00:00-24:00' 或 '22:00-05:00' 或 '00:00:00-24:00:00'
        :return: Boolean，True 表示在区间内，False 表示不在
        """
        if "-" not in time_range:
            raise ValueError(f"时间区间格式错误，必须包含 '-' 分隔符：{time_range}")

        start_str, end_str = time_range.split("-", 1)  # 限制分割一次，防止时间本身含横杠（虽不太可能）

        start_min = cls._parse_to_minutes(start_str)
        end_min = cls._parse_to_minutes(end_str)
        target_min = cls._parse_to_minutes(target_time)

        # 场景 A: 不跨天 (开始时间 <= 结束时间)
        # 例如：09:00 (540) - 18:00 (1080)
        # 逻辑：540 <= target <= 1080
        if start_min <= end_min:
            return start_min <= target_min <= end_min

        # 场景 B: 跨天 (开始时间 > 结束时间)
        # 例如：22:00 (1320) - 05:00 (300)
        # 有效区间为：[1320, 1440] U [0, 300]
        # 逻辑：target >= 1320 OR target <= 300
        # 注意：若 target 为 24:00 (1440)，在跨天场景下通常属于“前半夜”的结束，满足 >= start
        return (target_min >= start_min) or (target_min <= end_min)

    @classmethod
    def normalize_range(cls, time_range: str) -> str:
        """
        （可选辅助方法）将时间区间标准化为 HH:MM-HH:MM 格式，去除秒数。
        :param time_range: 原始区间字符串
        :return: 标准化后的字符串
        """
        if "-" not in time_range:
            return time_range

        start_str, end_str = time_range.split("-", 1)
        start_min = cls._parse_to_minutes(start_str)
        end_min = cls._parse_to_minutes(end_str)

        def mins_to_str(m):
            if m == 1440:
                return "24:00"
            h = m // 60
            m_rem = m % 60
            return f"{h:02d}:{m_rem:02d}"

        return f"{mins_to_str(start_min)}-{mins_to_str(end_min)}"


if __name__ == '__main__':
    # print(TimeUtil.convertSecsDuration(8))
    # print(TimeUtil.convertSecsDuration(59.5))
    # print(TimeUtil.convertSecsDuration(61))
    # print(TimeUtil.convertSecsDuration(3600 + 358))
    # print(TimeUtil.convertSecsDuration(5260))
    print(TimeUtil.is_time_greater_than("22:15:59"))
    print(TimeUtil.is_time_greater_than(" 2025-06-26     16:00:00  "))
    print(TimeUtil.is_time_greater_than("  2025-06-26   22:15:59  "))
    print(TimeUtil.is_time_greater_than("2025-06-26"))
    # print(TimeUtil.dateDiff('09:30:33', "09:30:00", '%H:%M:%S'))
    # print(TimeUtil.dateDiff('2025-06-14', "2025-06-18", '%Y-%m-%d'))
    # print(TimeUtil.getTimeStr('%Y-%m-%d', 1))  # 昨天
    # print(TimeUtil.getTimeStr('%Y-%m-%d', 0))  # 今天
    # print(TimeUtil.getTimeStr('%Y-%m-%d', -1))  # 明天
