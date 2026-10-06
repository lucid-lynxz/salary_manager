# !/usr/bin/env python3
# -*- coding:utf-8 -*-
import importlib.util
import importlib.util
import json
import logging
import os
import platform
import random
import re
import signal
import subprocess
import sys
import time
from typing import Type, Union, Optional, List, Set, Dict, Any, Pattern

import pandas as pd

Number = Union[int, float]


class CommonUtil(object):
    # @classmethod
    # def convertStringEncode(cls, srcStr, srcEncode: str = "utf-8", toEncode=sys.stdout.encoding):
    #     """
    #     将字符串转为另一种编码,默认从 utf-8 转为默认控制台的编码,避免中文乱码等问题
    #     :param srcStr:  要进行编码转换的字符串
    #     :param srcEncode:  原始编码
    #     :param toEncode:  目标编码
    #     :return:
    #     """
    #     return srcStr.decode(srcEncode).encode(toEncode)

    _redirect_log_fun = None

    @staticmethod
    def redirect_print_log(func):
        """设置外部日志重定向函数, 函数包含一个字符串入参"""
        CommonUtil._redirect_log_fun = func

    @staticmethod
    def redirect_file_log(log_name: str, log_dir: str):
        """
        启用默认的文件日志
        :param log_name: 日志文件名也是logger名字
        :param log_dir: 存储日志的目录
        """
        from util.log_handler import DefaultCustomLog
        _logger = DefaultCustomLog.get_log(log_name, f'{log_dir}/{log_name}.log', use_file_handler=True)
        CommonUtil._redirect_log_fun = _logger

    @staticmethod
    def updateStdOutEncoding(encoding: str = 'utf8'):
        """
        修改标准输出的编码为utf8,可解决部分终端中文乱码问题
        """
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding=encoding)

    @staticmethod
    def printLog(msg, condition: bool = True, prefix: str = "", includeTime: bool = True, prefer_redirect_log: bool = True):
        """
        打印日志, 默认使用print() 并会自动拼接当前时间信息, 打印格式为: {prefix}{时间信息}{msg}
        若设置了重定向函数, 并且 prefer_redirect_log=True, 则会使用重定向函数进行日志输出, 日志格式: {prefix}{msg}
        :param msg: 待打印的日志信息
        :param condition: 打印条件, 默认为True, false则不会打印
        :param prefix: 日志前缀, 默认为空
        :param includeTime: 是否包含时间信息, 默认为True, 默认的print有效
        :param prefer_redirect_log: 是否优先使用重定向函数进行日志输出, 默认为True
        """
        from util.TimeUtil import TimeUtil
        if condition:
            # 此处保持使用原始的 print() 语句
            try:
                if prefer_redirect_log and CommonUtil._redirect_log_fun is not None:
                    CommonUtil._redirect_log_fun(f'{prefix}{msg}')
                else:
                    ts = f"{TimeUtil.getTimeStr()} " if includeTime else ""
                    print(f"{prefix}{ts}{msg}", flush=True)
            except Exception as e:
                print(f"printLog exception {e}", flush=True)

    @classmethod
    def exeCmd(cls, cmd: str, printCmdInfo: bool = True) -> str:
        """
        执行shell命令, 可得到返回值
        :param cmd: 待执行的命令
        :param printCmdInfo: 是否打印命令内容
        """
        # CommonUtil.printLog("execute cmd: %s" % cmd, printCmdInfo)
        # readlines = os.popen(cmd).readlines()
        # result = "".join(readlines)
        # CommonUtil.printLog("result=%s" % result)
        # return result

        try:
            with os.popen(cmd) as fp:
                bf = fp._stream.buffer.read()
        except Exception as e:
            CommonUtil.printLog(f"exeCmd exception:{cmd}\n{e}".strip())
            return ""

        try:
            cmd_result = bf.decode('utf8', 'ignore').strip()
        except UnicodeDecodeError:
            cmd_result = bf.decode('gbk', 'ignore').strip()

        CommonUtil.printLog(f"exeCmd:{cmd}\n{cmd_result}".strip(), printCmdInfo)
        return cmd_result

    @classmethod
    def exeCmdBySubprocess(cls, cmd: str, printCmdInfo: bool = True, timeout: int = 30) -> str:
        """
        同步执行shell命令, 可得到返回值
        :param cmd: 待执行的命令
        :param printCmdInfo: 是否打印命令内容
        :param timeout: 超时时间, 单位秒, 默认30秒
        """
        try:
            # 使用subprocess模块执行命令，捕获标准输出和标准错误
            # shell=True表示在shell中执行命令
            # universal_newlines=True表示将输出视为文本而不是字节
            result = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # 合并标准错误到标准输出
                text=True,  # Python 3.7+ 使用text代替universal_newlines
                encoding=sys.getdefaultencoding(),  # 使用系统默认编码
                timeout=timeout  # 添加超时时间，防止命令卡死
            )

            # 获取命令的输出结果
            cmd_result = result.stdout.strip()

            # 打印命令信息和结果
            CommonUtil.printLog(f"exeCmd:{cmd}\n{cmd_result}".strip(), printCmdInfo)
            return cmd_result
        except subprocess.TimeoutExpired:
            CommonUtil.printLog(f"exeCmd 超时: {cmd}", printCmdInfo)
            return ""
        except Exception as e:
            CommonUtil.printLog(f"exeCmd exception:{cmd}\n{e}".strip(), printCmdInfo)
            return ""

    @staticmethod
    def exeCmdAsync(cmd: Union[str, List[str]], printLog: bool = False) -> subprocess.Popen:
        """
        异步执行shell命令, 可得到进程信息, 如pid (_process.pid)
        :param cmd: 待执行的命令, 对于单个命令, 可以直接传入字符串, 对于带参命令, 需要传入列表
            比如直接传入 'scrcpy' 可以运行该命令
            但若有带参, 如: 'scrcpy  --serial xxx --audio-source=output --audio-codec=aac --record abc.mp4' 在需要传入list, 以空格进行切分,最终如下:
            ['scrcpy', '--serial', 'xxx',  '--audio-source=output', '--audio-codec=aac', '--record', 'abc.mp4']
        """
        # _process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        _process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        CommonUtil.printLog(f"exeCmdAsync pid:{_process.pid},cmd={cmd}", printLog)
        return _process

    @staticmethod
    def getCmdAsyncResult(process: subprocess.Popen, timeout: int = None) -> tuple:
        """
        获取异步执行命令的结果
        :param process: exeCmdAsync返回的进程对象
        :param timeout: 超时时间(秒), 默认不超时
        :return: (returncode, output) 其中output为命令输出内容
        """
        try:
            output, _ = process.communicate(timeout=timeout)
            # 尝试UTF-8解码，失败则使用安全解码
            try:
                output_str = output.decode('utf-8')
            except UnicodeDecodeError:
                output_str = CommonUtil.safe_decode(output)
            return process.returncode, output_str.strip()
        except subprocess.TimeoutExpired:
            process.kill()
            return -1, f"Command timed out after {timeout} seconds"
        except Exception as e:
            return -1, f"Error getting command result: {str(e)}"

    @staticmethod
    def stopProcess(process, print_log: bool = True):
        try:
            if sys.platform == 'win32':
                # Windows approach
                process.terminate()
            else:
                # Unix/Linux approach
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            CommonUtil.printLog(f"killPid success:{process.pid}", print_log)
        except Exception as e:
            print(f"Error stopping process: {e}")
            return False
        return True

    @staticmethod
    def killPid(pid: int, print_log: bool = True) -> bool:
        try:
            # 发送终止信号
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            CommonUtil.printLog(f"killPid success:{pid}", print_log)
            return True
        except Exception as e:
            CommonUtil.printLog(f"killPid exception:{pid}\n{e}".strip())
            return False

    @classmethod
    def exeCmdByOSSystem(cls, cmd: str, printCmdInfo: bool = True):
        """
        通过os.system(xxx) 执行命令, 无返回信息
        """
        CommonUtil.printLog("execute cmd by os.system: %s" % cmd, printCmdInfo)
        os.system(cmd)

    @classmethod
    def isNoneOrBlank(cls, info) -> bool:
        """
        判断所给字符串或其他带有len()方法的对象是否为None或者空(空白字符),若是则返回True
        :param info: 待判断的带有len()方法的对象, 若是字符串,则会进行strip()后再处理
        :return:
        """
        if info is None or (isinstance(info, float) and pd.isna(info)):
            return True

        elif isinstance(info, str):
            return len(info.strip()) == 0
        elif (isinstance(info, dict)
              or isinstance(info, list)
              or isinstance(info, tuple)
              or isinstance(info, set)
              or isinstance(info, bytes)
              or isinstance(info, bytearray)):
            return len(info) == 0
        elif isinstance(info, (int, float, bool)):
            return False
        elif isinstance(info, pd.Series):
            return info.astype(str).str.strip().isin(['', 'nan']).all()
        else:
            return False

    @classmethod
    def changeSep(cls, src: str) -> str:
        """
        将指定路径的分隔符('/'),替换成系统标准的分隔符
        :param src: 原路径
        :return: 处理的路径str
        """
        return src.replace('/', os.sep)

    @classmethod
    def convert2str(cls, src: object, encoding: str = 'utf8') -> str:
        """
        将源数据转换为str
        """
        if isinstance(src, bytes):
            return bytes.decode(src, encoding=encoding)
        elif isinstance(src, str):
            return src
        elif isinstance(src, (int, float, bool)):
            return str(src)
        raise Exception('convert2str fail unsupport type:%s, value=%s' % (type(src), src))

    @staticmethod
    def recookPath(path: str, forceEnableLongPath: bool = False) -> str:
        """未避免循环导包,此处改为方法内import"""
        from util.FileUtil import FileUtil
        return FileUtil.recookPath(path, forceEnableLongPath)

    @staticmethod
    def isFileExist(path: str) -> bool:
        """
        与 FileUtil.isFileExist() 功能一致，为避免互相引用导致 ImportError，此处复制一个冗余方法
        功能：文件是否存在
        :param path: 文件路径
        :return: bool
        """
        path = CommonUtil.recookPath(path)
        if path is None or len(path) == 0:
            return False
        return os.path.exists(path)

    @staticmethod
    def getPlatformName() -> str:
        """
        获取当前系统名，固定返回小写字符串,如： windows linux macos
        """
        name = platform.system().lower()
        if 'darwin' == name:
            name = 'macos'
        return name

    @staticmethod
    def isWindows() -> bool:
        """
        当前是否是windows系统
        """
        return 'windows' == CommonUtil.getPlatformName()

    @staticmethod
    def isLinux() -> bool:
        return 'linux' == CommonUtil.getPlatformName()

    @staticmethod
    def isMacOS() -> bool:
        return 'macos' == CommonUtil.getPlatformName()

    @staticmethod
    def recookExeSuffix(path: str) -> str:
        """
        仅对文件(非目录)路径有效
        若是windows系统，则按需添加文件名后缀 .exe
        否则，删除存在的 .exe 后缀
        """
        if CommonUtil.isNoneOrBlank(path) or path.endswith('//') or path.endswith('\\'):
            return path

        if CommonUtil.isWindows():
            if not path.endswith('.exe'):
                path = '%s.exe' % path
        elif path.endswith('.exe'):  # 非windows系统时，删除 .exe 后缀
            path = path[:-4]
        return path

    @staticmethod
    def checkThirdToolPath(path: str, returnValueIfFileNotExist: str = '', autoAddExeSuffix: bool = False) -> str:
        """
        部分三方工具程序已內置到本项目中，见 `third_tools/` 目录
        本方法检测配置文件所提供的路径是否是内置路径，格式为：格式为：'third_tools/{工具目录名}/*/{可执行程序名}'
        如： third_tools/7z/*/7z 或者 third_tools/7z/*/7z.exe 表示7z可执行程序
        本方法会自动识别当前系统平台，并替换上述 ‘*’ 星号为具体平台名，同时仅windows平台才添加 .exe 后缀
        @param path:原始路径
        @param returnValueIfFileNotExist: 若处理后的程序文件不存在，则返回的值，默认返回空
        @param autoAddExeSuffix: 若是win系统则自动添加后缀 .exe， 否则删除 .exe 后缀
        @param 处理后的路径
        """
        path = CommonUtil.recookPath(path)
        if path.startswith('third_tools/') and '*' in path:
            platformName = CommonUtil.getPlatformName()
            path = path.replace('/*/', '/%s/' % platformName)
            path = CommonUtil.recookExeSuffix(path)

            # 拼接项目根目录路径，生成绝对路径
            root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = '%s/%s' % (root_path, path)

        if not CommonUtil.isFileExist(path):
            path = returnValueIfFileNotExist

        if autoAddExeSuffix:
            path = CommonUtil.recookExeSuffix(path)
        path = CommonUtil.recookPath(path)
        CommonUtil.printLog('checkThirdToolPath result=%s' % path)
        return path

    @staticmethod
    def convertStr2Float(src: Union[str, float], defaultValue: float) -> float:
        if isinstance(src, float):
            return src

        if CommonUtil.isNoneOrBlank(src):
            return defaultValue

        try:
            return float(src)
        except ValueError:
            return defaultValue

    @staticmethod
    def convertStr2Int(src: Union[str, int], defaultValue: int) -> int:
        if isinstance(src, int):
            return src

        if CommonUtil.isNoneOrBlank(src):
            return defaultValue

        try:
            return int(src)
        except ValueError:
            return defaultValue

    @staticmethod
    def killPid(pid: str):
        """
        结束指定pid的进程
        """
        if CommonUtil.isNoneOrBlank(pid):
            return

        try:
            if CommonUtil.isWindows():
                CommonUtil.exeCmdByOSSystem("taskkill /f /pid %s" % pid)
            else:
                import signal
                os.kill(int(pid), signal.SIGKILL)
                CommonUtil.printLog(f'killPid {pid}')
        except Exception as e:
            CommonUtil.printLog(f'killPid {pid} fail: {e}')

    @staticmethod
    def is_library_installed(library_name: Union[str, list]) -> bool:
        """
        检查给定的库是否已安装
        :param library_name: 库名 如 PIL (对应pillow包), 支持传一个str, 也支持传多个 list
        """
        if isinstance(library_name, list):
            for name in library_name:
                if not CommonUtil.is_library_installed(name):
                    return False
            return True
        return importlib.util.find_spec(library_name) is not None

    @staticmethod
    def get_module_path(module_name):
        """
        获取指定库的所在路径
        """
        spec = importlib.util.find_spec(module_name)
        if spec is not None:
            return spec.origin
        else:
            return None

    @staticmethod
    def get_input_info(tip: str, def_value: str, timeout: int = None, value_timeout: str = None, quit_flag: str = "q") -> str:
        """
        获取用户输入的值
        :param tip: 提示语
        :param def_value:输入为空时,返回的默认值
        :param timeout: 超时时间(秒), 超时后 value_timeout, None表示不超时
        :param value_timeout: 超时时返回的值, 若为None,则使用 def_value
        :param quit_flag: 退出字符, 输入该字符则退出程序
        :return:
        """
        if timeout is None:
            # 不超时，使用标准 input
            msg = input(tip).strip()
            _result = def_value
            if len(msg) > 0:
                _result = msg
            if quit_flag is not None and quit_flag == _result:
                exit(0)
            return _result
        else:
            # 支持超时
            import threading
            if value_timeout is None:
                value_timeout = def_value
            result = [value_timeout]  # 使用列表存储结果，以便在子线程中修改

            def get_input():
                try:
                    msg = input(tip).strip()
                    if len(msg) > 0:
                        result[0] = msg
                except:
                    pass

            # 创建子线程获取输入
            input_thread = threading.Thread(target=get_input)
            input_thread.daemon = True  # 设置为守护线程
            input_thread.start()

            # 等待线程结束或超时
            input_thread.join(timeout=timeout)

            # 检查是否超时
            if input_thread.is_alive():
                # 超时，打印提示
                print(f"\n输入超时({timeout}秒)，使用值: {value_timeout}")
                # 由于 input() 仍在等待，需要强制结束线程（但 Python 线程无法强制结束）
                # 这里我们只能返回默认值，input() 会在用户按下回车后才继续
                # 这是一个已知的 Python 限制

            _result = result[0]
            if quit_flag is not None and quit_flag == _result:
                exit(0)
            return _result

    @staticmethod
    def get_input_with_timeout(prompt: str, default: str = "", timeout: int = 5) -> str:
        """
        获取用户输入，支持超时机制
        注意：由于 Python 的 input() 限制，超时后用户仍需按回车键才能继续
        :param prompt: 提示信息
        :param default: 超时或输入为空时的默认值
        :param timeout: 超时时间（秒）
        :return: 用户输入或默认值
        """
        import threading

        result = [default]

        def input_thread_func():
            try:
                user_input = input(prompt)
                result[0] = user_input.strip() if user_input.strip() else default
            except:
                pass

        thread = threading.Thread(target=input_thread_func)
        thread.daemon = True
        thread.start()

        thread.join(timeout=timeout)

        if thread.is_alive():
            print(f"\n[超时 {timeout}秒，使用默认值: {default}]")
            print("提示: 请按回车键继续...")

        return result[0]

    @staticmethod
    def parse_number(
            s: str,
            target_type: Type[Number] = float,
            default: Optional[Number] = 0
    ) -> Optional[Number]:
        """
        从字符串中提取数值部分并解析为指定类型，失败时返回默认值
        使用示例: parse_number("(123.45)", int, 0)

        参数:
            s: 包含数值的字符串
            target_type: 目标类型，可选int或float（默认float）
            default: 解析失败时的默认值（默认None）

        返回:
            解析后的数值（类型为target_type），或默认值
        """
        if not isinstance(s, str) or not s.strip():
            return default

        s = s.strip()

        # 使用正则表达式提取连续的数值部分
        match = re.search(r'[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?', s)

        if not match:
            return default

        num_str = match.group(0)

        try:
            if target_type is int:
                # 对于int类型，先尝试直接转换（处理整数情况）
                return int(num_str)
            elif target_type is float:
                # 对于float类型，先转换为float再检查是否为整数
                num = float(num_str)
                return int(num) if num.is_integer() and target_type is int else num
            else:
                raise ValueError(f"Unsupported target type: {target_type}")
        except ValueError:
            return default

    @staticmethod
    def round_down_to_hundred(num: float) -> int:
        """将浮点数向下取整到最近的100整数倍"""
        return int(num // 100) * 100

    @staticmethod
    def extract_keys(lines: list, target_keys: list, flag: str = '=') -> dict:
        """
        从字符串列表中提取指定 key 的值，返回字典, 行格式默认: key=value
        @param lines: 包含字符串的列表, 每个元素都是str类型, 格式: key=value, 允许有空格
        @param  target_keys: 需要提取的 key 列表
        @param flag: 分隔符
        @return: 包含 key-value 的字典
        """
        result = {}
        if CommonUtil.isNoneOrBlank(lines) or CommonUtil.isNoneOrBlank(target_keys):
            return result

        for line in lines:
            line = line.strip()
            if not line or flag not in line:
                continue

            key_part, value_part = line.split(flag, 1)
            key = key_part.strip()
            value = value_part.strip()

            if key in target_keys:
                result[key] = value
        return result

    @staticmethod
    def set_windows_brightness(brightness: int) -> bool:
        """
        设置Windows系统屏幕亮度（0-100）
        需要安装wmi库: pip install wmi
        :param brightness: 亮度百分比（0-100）
        :return 是否设置成功
        """
        if CommonUtil.isWindows() and not CommonUtil.is_library_installed('wmi'):
            CommonUtil.printLog(f'请安装wmi库: pip install wmi')
            return False

            # 检查亮度值是否在有效范围内
        if not (0 <= brightness <= 100):
            raise ValueError("亮度值必须在0-100之间")

        try:
            # 连接到WMI服务
            import wmi
            c = wmi.WMI(namespace='wmi')
            # 获取显示器亮度类实例
            monitor = c.WmiMonitorBrightnessMethods()[0]
            # 设置亮度
            monitor.WmiSetBrightness(brightness, 1)  # 第二个参数是超时时间（秒）
            return True
        except Exception as e:
            CommonUtil.printLog(f"设置亮度时出错: {e}")

    @staticmethod
    def windows_sleep():
        """让 Windows 系统进入休眠状态"""
        if not CommonUtil.isWindows():
            return
        try:
            # 调用 Windows 系统命令实现休眠
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                check=True,
                shell=True
            )
            CommonUtil.printLog("系统已进入休眠状态")
        except subprocess.CalledProcessError as e:
            CommonUtil.printLog(f"休眠命令执行失败: {e}")
        except Exception as e:
            CommonUtil.printLog(f"发生错误: {e}")

    @staticmethod
    def safe_decode(bytes_data):
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin1']
        for encoding in encodings:
            try:
                return bytes_data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return bytes_data.decode('utf-8', errors='replace')

    @staticmethod
    def find_system_chinese_font() -> Optional[str]:
        """
        自动查找系统中支持中文的字体

        Returns:
            str: 找到的字体文件路径，找不到则返回None
        """
        # 根据不同操作系统查找可能的中文字体
        system = platform.system()

        if system == "Windows":
            # Windows系统常见中文字体路径
            font_paths = [
                r"C:\Windows\Fonts\simsun.ttc",  # 宋体
                r"C:\Windows\Fonts\simhei.ttf",  # 黑体
                r"C:\Windows\Fonts\msyh.ttc",  # 微软雅黑
                r"C:\Windows\Fonts\msyhbd.ttc",  # 微软雅黑加粗
                r"C:\Windows\Fonts\simkai.ttf",  # 楷体
                r"C:\Windows\Fonts\simfang.ttf"  # 仿宋
            ]
        elif system == "Darwin":  # macOS
            font_paths = [
                "/System/Library/Fonts/PingFang.ttc",  # 苹方
                "/System/Library/Fonts/SFNS.ttc",  # San Francisco
                "/Library/Fonts/Arial Unicode.ttf"
            ]
        else:  # Linux等其他系统
            font_paths = [
                "/usr/share/fonts/wqy/wqy-microhei.ttc",  # 文泉驿微米黑
                "/usr/share/fonts/wqy/wqy-zenhei.ttc",  # 文泉驿正黑
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
            ]

        # 检查是否存在这些字体文件
        for font_path in font_paths:
            if os.path.exists(font_path):
                return font_path
        return None

    @staticmethod
    def generate_random_numbers(count: int, min_val: int = 0, max_val: int = None, print_exclude_set: bool = False) -> Set:
        """
        在指定范围 [min_val,max_val] 内生成指定个数的不重复随机数, 返回一个 Set() 集合
        可自行排序:  sorted(random_numbers)

        Args:
            count: 需要生成的随机数个数
            min_val: 最小值（包含），默认为0
            max_val: 最大值（包含），默认为count-1+min_val
            print_exclude_set: 是否打印排除的数字集合，默认为False

        Returns:
            set: 包含不重复随机数的集合
        """
        if max_val is None:
            max_val = count - 1 + min_val

        total_range = max_val - min_val + 1

        # 检查范围是否足够容纳所需数量的数字
        if total_range < count:
            raise ValueError("范围太小，无法生成指定数量的不重复数字")

        # 当需要生成的数字接近总数时，使用反向策略更高效
        if count > total_range * 0.9:  # 当超过90%时使用反向策略
            # 生成需要排除的数字
            exclude_count = total_range - count
            excluded_numbers = set(random.sample(range(min_val, max_val + 1), exclude_count))
            if print_exclude_set:
                CommonUtil.printLog(f"排除的数字集合: {excluded_numbers}")
            # 生成全量集合并减去排除的数字
            full_range = set(range(min_val, max_val + 1))
            return full_range - excluded_numbers
        else:
            # 正常采样策略
            result_set = set(random.sample(range(min_val, max_val + 1), count))
            if print_exclude_set:
                CommonUtil.printLog(f"排除的数字集合: {set(range(min_val, max_val + 1)) - result_set}")
            return result_set

    @staticmethod
    def format_dict(data: Union[List, Dict], json_mode: bool = True, json_indent: int = 2, kv_sep_flag: str = ', ') -> str:
        """
        格式化dict, 支持两种结果:
        1. json格式, 并根据指定的缩进字符排列
        2. key:value 格式, 不同kv之间通过  kv_sep_flag 进行连接
        如:  {'name': 'lynxz', 'age': '38'}, 可输出以下:
        {
            'name': 'lynxz',
            'age': '38'
        }

        或者: name:lynxz, age:38

        @param data 待格式化的dict数据
        @param json_mode 是否数据json数据
        @param json_indent json缩进字符数
        @param kv_sep_flag  key-value连接符, json_mode=False时使用
        """
        _is_empty = CommonUtil.isNoneOrBlank(data)
        if json_mode:
            return '{}' if _is_empty else json.dumps(data, indent=json_indent, ensure_ascii=False)
        return '' if _is_empty else kv_sep_flag.join(f"{k}: {v}" for k, v in data.items())

    @staticmethod
    def starts_with_chinese(text) -> bool:
        """检测文本是否以中文字符开头"""
        if not text:
            return False
        first_char = text[0]
        # 中文字符的 Unicode 范围
        return '\u4e00' <= first_char <= '\u9fff'

    @staticmethod
    def contains_chinese(text) -> bool:
        """检测文本是否包含中文字符"""
        if not text:
            return False
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False

    @staticmethod
    def is_lon_lat(text) -> bool:
        """判断字符串是否为经纬度格式"""
        # 匹配模式：数字.数字,数字.数字 或 数字,数字
        pattern = r'^-?\d+\.?\d*,\s*-?\d+\.?\d*$'
        return bool(re.match(pattern, text))

    _logger = None  # 即写入日志文件也打印到控制台
    _file_only_logger = None  # 只写入到文件
    _console_only_logger = None  # 只输出到控制台的 logger

    @staticmethod
    def setup_logging(log_dir: str, log_name: str, fmt: str = '%(asctime)s %(message)s'):
        """
        设置日志记录器
        @param log_dir: 日志目录
        @param log_name: 日志文件名, 会自动追加 .log 后缀
        @param fmt: 日志格式
        """
        if CommonUtil._logger is not None:
            return CommonUtil._logger

        # 使用点号而不是逗号的 formatter
        # formatter = logging.Formatter(fmt)
        class DotMillisecondFormatter(logging.Formatter):
            def formatTime(self, record, datefmt=None):
                if datefmt:
                    s = time.strftime(datefmt, self.converter(record.created))
                else:
                    t = time.strftime(self.default_time_format, self.converter(record.created))
                    s = "%s.%03d" % (t, record.msecs)  # 使用点号
                return s

        formatter = DotMillisecondFormatter(fmt)

        # 创建共享的 FileHandler
        file_handler = logging.FileHandler(f'{log_dir}/{log_name}.log', encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        # 创建只输出到控制台的 StreamHandler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        # 配置主 logger，同时输出到控制台和日志文件
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=[file_handler, console_handler])
        CommonUtil._logger = logging.getLogger(log_name)

        # 创建一个只写入文件的日志记录器，用于需要静默记录的日志
        CommonUtil._file_only_logger = logging.getLogger(f'{log_name}_file_only')
        CommonUtil._file_only_logger.setLevel(logging.INFO)
        CommonUtil._file_only_logger.addHandler(file_handler)  # 共享同一个 FileHandler
        CommonUtil._file_only_logger.propagate = False  # 不传播到父日志记录器

        # 创建一个只输出到控制台的日志记录器，不写入文件
        CommonUtil._console_only_logger = logging.getLogger(f'{log_name}_console_only')
        CommonUtil._console_only_logger.setLevel(logging.INFO)
        CommonUtil._console_only_logger.addHandler(console_handler)  # 只添加 StreamHandler
        CommonUtil._console_only_logger.propagate = False  # 不传播到父日志记录器

        def redirect_log(msg):
            try:
                CommonUtil._logger.info(msg)
            except Exception as e:
                CommonUtil.printLog(f'redirect_log 出错:{e}', prefer_redirect_log=False)

        CommonUtil.redirect_print_log(redirect_log)

        # 重定向 sys.stdout 使所有 print() 语句都写入日志文件（不输出到控制台，避免重复）
        class StdoutRedirector:
            def __init__(self, original_stdout):
                self.original_stdout = original_stdout
                self.logger = CommonUtil._file_only_logger  # 使用只写入文件的 logger

            def write(self, text):
                if text.strip():  # 忽略空行
                    self.logger.warning(text.rstrip('\n'))
                self.original_stdout.write(text)

            def flush(self):
                self.original_stdout.flush()

        sys.stdout = StdoutRedirector(sys.stdout)
        return CommonUtil._logger

    @staticmethod
    def print(msg: str, target: int = 0, condition: bool = True):
        """
        打印日志
        @param msg: 打印内容
        @param target: 打印目标
                    0: 全部, 既输出到控制台, 也写入到文件
                    1: 只输出到控制台
                    2: 只写入到文件
                    注意: 需要先触发 setup_logging() 后才支持
        @param condition: 是否允许打印日志
        """
        if not condition:
            return

        if CommonUtil._logger is None or target == 0:
            CommonUtil.printLog(msg)
            return

        if target == 1:  # 只输出到控制台
            CommonUtil._console_only_logger.info(msg)
        elif target == 2:  # 只写入到文件
            CommonUtil._file_only_logger.info(msg)
        else:
            CommonUtil.printLog(msg)

    @staticmethod
    def pad_strings(strings: List[str], pad_side: str = 'right') -> List[str]:
        """
        将字符串列表中所有元素的长度补齐到相同长度

        Args:
            strings: 字符串列表
            pad_side: 填充位置，可选值:
                      - 'right': 在右侧填充空格（默认）
                      - 'left': 在左侧填充空格
                      - 'center': 在中间填充空格

        Returns:
            list[str]: 补齐后的字符串列表，所有元素长度相同

        Example:
            pad_strings(['a', 'bb', 'ccc'])
            ['a  ', 'bb ', 'ccc']

            pad_strings(['a', 'bb', 'ccc'], pad_side='left')
            ['  a', ' bb', 'ccc']
        """
        if not strings:
            return []

        # 找到最长字符串的长度
        max_len = max(len(s) for s in strings)

        # 补齐所有字符串
        return [CommonUtil.pad_string(s, max_len, pad_side) for s in strings]

    @staticmethod
    def pad_string(s: str, max_len: int, pad_side: str = 'right') -> str:
        """
        将字符串补齐到指定长度
        @param s: 字符串
        @param max_len: 最大长度
        @param pad_side: 填充位置，可选值:
                         - 'right': 在右侧填充空格（默认）
                         - 'left': 在左侧填充空格
                         - 'center': 在中间填充空格
        """
        # 补齐所有字符串
        if pad_side == 'right':
            result = s.ljust(max_len)
        elif pad_side == 'left':
            result = s.rjust(max_len)
        elif pad_side == 'center':
            result = s.center(max_len)
        else:
            raise ValueError(f"pad_side 必须是 'right', 'left' 或 'center'，当前值为: {pad_side}")
        return result

    @staticmethod
    def filter_dict_list(
            data: List[Dict[str, Any]],
            conditions: Dict[str, Union[Any, Pattern, str]],
            mode: str = 'all',
            use_regex: bool = False
    ) -> List[Dict[str, Any]]:
        """
        过滤字典列表

        Args:
            data: 字典列表
            conditions: 过滤条件字典，key 为属性名，value 为期望的值, 支持过滤的属性类型: int/bool/float/str
                       - 普通值：直接相等比较
                       - callable: 调用函数进行判断
                       - 正则字符串/Pattern: 当 use_regex=True 时用于字符串匹配
                       示例:  {
                       'intFloatKey': lambda x: x > 10000,   # float/int类型, 支持掺入lambda顾虑条件
                       'strKey' : r'Lynxz',                 # 支持过滤str属性值中包含 'Lynxz' 的数据
                       'boolKey': True                     # 对布尔属性值进行过滤, 保留值为 True 的数据
                       }

            mode: 'all' - 满足所有条件; 'any' - 满足任一条件
            use_regex: 是否启用正则匹配 (仅对字符串属性有效), 支持两种格式:
                        - 字符串形式: {'name': r'^iPhone'}
                        - 预编译 Pattern: {'name': re.compile(r'Air', re.IGNORECASE)}
        Returns:
            过滤后的新列表
        """

        def check_condition(item: Dict[str, Any]) -> bool:
            """检查单个字典是否满足条件"""
            results = []
            for key, expected_value in conditions.items():
                if key not in item:
                    results.append(False)
                    continue

                actual_value = item[key]

                # 如果条件是 callable，则调用它
                if callable(expected_value):
                    results.append(expected_value(actual_value))
                # 如果启用正则且值是字符串类型
                elif use_regex and isinstance(actual_value, str):
                    # 编译正则表达式 (如果还不是 Pattern 对象)
                    pattern = expected_value if isinstance(expected_value, Pattern) else re.compile(expected_value)
                    results.append(bool(pattern.search(actual_value)))
                else:
                    # 否则进行相等性比较
                    results.append(actual_value == expected_value)

            return all(results) if mode == 'all' else any(results)

        return [item for item in data if check_condition(item)]

    @staticmethod
    def merge_dict(dict_left: dict, dict_right: dict, priority: str = 'merge') -> dict:
        """
        合并两个字典，相同 key 的 value 进行拼接
        - 字符串：直接拼接
        - 列表：合并列表
        - 其他类型：转为字符串后拼接
        @param dict_left: 左字典
        @param dict_right: 右字典
        @param priority: 存在相同key时的处理策略: merge:合并  left: 左优先 right: 右优先
        """
        priority = priority.lower().strip()
        if priority not in ['merge', 'left', 'right']:
            priority = 'merge'

        result = dict_left.copy()

        for key, value in dict_right.items():
            if key in result:
                # 相同 key，拼接 value
                existing_value = result[key]

                if priority == 'left':
                    result[key] = existing_value
                elif priority == 'right':
                    result[key] = value
                else:
                    # 根据类型进行拼接
                    if isinstance(existing_value, list) and isinstance(value, list):
                        result[key] = existing_value + value
                    elif isinstance(existing_value, str) and isinstance(value, str):
                        result[key] = existing_value + value
                    else:
                        # 其他类型转为字符串拼接
                        result[key] = f"{existing_value}{value}"
            else:
                # 不同 key，直接添加
                result[key] = value

        return result

    @staticmethod
    def filter_list(
            data: List[str],
            patterns: Dict[str, str],
            flags: int = 0,
            strip_result: bool = True,
            start_line: int = 0,
            start_pattern: str = None,
            line_mode: bool = True
    ) -> Dict[str, List[str]]:
        """
        从字符串列表中提取符合多个正则表达式的内容，返回分组结果
        主要用于对日志进行过滤，提取关键信息
        
        :param data: 原始字符串列表
        :param patterns: 正则表达式字典，格式为 {简写名称: 正则表达式}
                        正则中必须包含捕获组 () 用于提取目标内容
                        例如: {'A': r'.*appId:(.*)$', 'B': r'.*appSecret:\s*(\w+)end$'}
        :param flags: 正则标志位，如 re.IGNORECASE（默认 0）
        :param strip_result: 是否去除提取结果的空白字符（默认 True）
        :param start_line: 从第几行开始生效（从 0 开始计数），默认 0 表示全部有效
                          例如: start_line=10 表示跳过前 10 行，从第 11 行开始过滤
        :param start_pattern: 起始匹配模式，找到第一个匹配该模式的行后开始生效
                             优先级高于 start_line，若两者都设置，以 start_pattern 为准
                             例如: start_pattern=r'=== TestCase Start ===' 表示从测试用例开始处过滤
        :param line_mode: 是否按行过滤，默认 True, 若为False, 会先合并有效的数据行, 再进行一次性匹配, 以便支持跨行匹配的需求
        :return: 字典，key 为正则简写名称，value 为匹配到的内容列表
                例如: {'A': ['123', '456'], 'B': ['abc37']}
        
        :example:
        # 示例 1: 基础用法
        lines = [
             'helloAppId:123',
             '03:08:15 your appSecret: abc37end',
             'newAppId:456'
         ]
        patterns = {
             'A': r'.*appId:(.*)$',
             'B': r'.*appSecret:\s*(\w+)end$'
         }
        result = filter_list(lines, patterns)
        print(result)
        {'A': ['123', '456'], 'B': ['abc37']}
        
        # 示例 2: 不区分大小写
        result = filter_list(lines, patterns, flags=re.IGNORECASE)
        
        # 示例 3: 保留空白字符
        result = filter_list(lines, patterns, strip_result=False)
        
        # 示例 4: 复杂正则提取
        logs = [
             '[ERROR] userId=1001 message=login failed',
             '[INFO] userId=1002 message=success',
             '[WARN] userId=1003 message=timeout'
         ]
        patterns = {
             'user_id': r'userId=(\d+)',
             'error_msg': r'\[ERROR\].*message=(.*)',
             'all_msg': r'message=(.*)'
         }
        result = filter_list(logs, patterns)
        print(result)
        {
            'user_id': ['1001', '1002', '1003'],
            'error_msg': ['login failed'],
            'all_msg': ['login failed', 'success', 'timeout']
        }
        
        # 示例 5: 从第 10 行开始过滤（跳过前 10 行）
        result = filter_list(log_lines, patterns, start_line=10)
        
        # 示例 6: 从包含特定标记的行开始过滤
        result = filter_list(
            log_lines, 
            patterns, 
            start_pattern=r'=== TestCase Start ==='
        )
        """
        # 初始化结果字典
        result = {key: [] for key in patterns.keys()}
        if CommonUtil.isNoneOrBlank(data):
            return result

        # 预编译所有正则表达式，提升性能
        compiled_patterns = {}
        for key, pattern in patterns.items():
            try:
                compiled_patterns[key] = re.compile(pattern, flags)
            except re.error as e:
                CommonUtil.printLog(f"❌ 正则表达式编译失败 [{key}]: {pattern}\n错误信息: {e}")
                continue

        # 确定起始行索引
        actual_start_index = start_line
        if start_pattern is not None:
            # 使用 start_pattern 查找起始行
            try:
                start_regex = re.compile(start_pattern, flags)
                found = False
                for i, line in enumerate(data):
                    if isinstance(line, str) and start_regex.search(line):
                        actual_start_index = i
                        found = True
                        CommonUtil.printLog(f"📍 找到起始标记行 [{i}]: {line.strip()[:80]}")
                        break

                if not found:
                    CommonUtil.printLog(f"⚠️ 未找到起始标记: {start_pattern}，将从头开始过滤")
                    actual_start_index = 0
            except re.error as e:
                CommonUtil.printLog(f"❌ 起始标记正则编译失败: {start_pattern}\n错误信息: {e}")
                actual_start_index = 0

        # 确保起始索引有效
        actual_start_index = max(0, min(actual_start_index, len(data)))

        if actual_start_index > 0:
            CommonUtil.printLog(f"📋 跳过前 {actual_start_index} 行，从第 {actual_start_index + 1} 行开始过滤")

        if not line_mode:
            data = ['\n'.join(data[actual_start_index:])]
            actual_start_index = 0

        # 遍历数据（从起始行开始）
        for line_idx in range(actual_start_index, len(data)):
            line = data[line_idx]
            if not isinstance(line, str):
                continue

            # 对每个正则进行匹配
            combine_flag = ' ' if line_mode else ''
            for key, compiled_pattern in compiled_patterns.items():
                extracted = None
                match_list = compiled_pattern.findall(line)
                if not CommonUtil.isNoneOrBlank(match_list):
                    try:
                        # 处理 findall 返回元组的情况（当正则中有捕获组时）
                        if match_list and isinstance(match_list[0], tuple):
                            # 元组转字符串：取第一个非空元素或拼接所有元素
                            extracted = combine_flag.join([str(item) for item in match_list[0] if item])
                        else:
                            extracted = combine_flag.join(match_list)
                    except Exception as e:
                        CommonUtil.printLog(f"❌ 正则提取失败 [{key}]: {line.strip()[:80]}\n错误信息: {e}")

                # 根据参数决定是否去除空白
                if strip_result and extracted:
                    extracted = extracted.strip()

                # 添加到结果列表
                if not CommonUtil.isNoneOrBlank(extracted):
                    result[key].append(extracted)
        return result

    @staticmethod
    def deduplicate_list(data: List[str]) -> List[str]:
        """
        列表去重，保留首次出现的元素，保持原有顺序
        
        :param data: 原始列表（可能包含重复元素）
        :return: 去重后的列表，顺序与首次出现顺序一致
        
        :example:
        data = ['apple', 'banana', 'apple', 'orange', 'banana', 'grape']
        result = deduplicate_list(data)
        print(result)
        ['apple', 'banana', 'orange', 'grape']
        
        # 日志去重场景
        logs = ['ErrorCode:1001', 'ErrorCode:1002', 'ErrorCode:1001', 'ErrorCode:1003']
        unique_logs = deduplicate_list(logs)
        print(unique_logs)
        ['ErrorCode:1001', 'ErrorCode:1002', 'ErrorCode:1003']
        """
        return list(dict.fromkeys(data))

    @staticmethod
    def parse_cookie(cookie_source: str, from_file: bool = False) -> Dict[str, str]:
        """
        解析 Cookie 内容为字典

        :param cookie_source: Cookie 来源
                             - 如果 from_file=True，则为文件路径
                             - 如果 from_file=False，则为 Cookie 字符串
        :param from_file: 是否从文件读取，默认 False
                         - True: cookie_source 为文件路径，读取文件内容后解析
                         - False: cookie_source 为 Cookie 字符串，直接解析
        :return: Cookie 字典，key 为 Cookie 名称，value 为 Cookie 值
                - 解析失败返回空字典 {}

        :example:
        # 示例 1: 直接解析 Cookie 字符串
        cookie_str = 'session=abc123; user_id=1001; token=xyz789'
        result = parse_cookie(cookie_str)
        # 返回: {'session': 'abc123', 'user_id': '1001', 'token': 'xyz789'}

        # 示例 2: 从文件读取并解析
        result = parse_cookie('cookies.txt', from_file=True)
        # 返回: {'session': 'abc123', 'user_id': '1001', ...}

        # 示例 3: 多行 Cookie 格式（常见于浏览器导出）
        cookie_str = '''
        session=abc123
        user_id=1001
        token=xyz789
        '''
        result = parse_cookie(cookie_str)
        # 返回: {'session': 'abc123', 'user_id': '1001', 'token': 'xyz789'}

        # 示例 4: 带属性的 Cookie（自动忽略属性部分）
        cookie_str = 'session=abc123; Path=/; HttpOnly; user_id=1001'
        result = parse_cookie(cookie_str)
        # 返回: {'session': 'abc123', 'user_id': '1001'}
        """
        try:
            # 如果从文件读取，先读取文件内容
            if from_file:
                if not CommonUtil.isFileExist(cookie_source):
                    CommonUtil.printLog(f"❌ Cookie 文件不存在: {cookie_source}")
                    return {}

                with open(cookie_source, 'r', encoding='utf-8') as f:
                    cookie_content = f.read()
            else:
                cookie_content = cookie_source

            # 检查内容是否为空
            if CommonUtil.isNoneOrBlank(cookie_content):
                CommonUtil.printLog("⚠️ Cookie 内容为空")
                return {}

            result = {}

            # 按行分割处理（支持多行格式）
            lines = cookie_content.strip().splitlines()

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    # 跳过空行和注释行
                    continue

                # 尝试按分号分割（标准 Cookie 格式）
                if ';' in line:
                    pairs = line.split(';')
                    for pair in pairs:
                        pair = pair.strip()
                        if '=' in pair:
                            key, value = pair.split('=', 1)
                            key = key.strip()
                            value = value.strip()

                            # 过滤掉 Cookie 属性（如 Path, Domain, HttpOnly 等）
                            if key and value and key.lower() not in ['path', 'domain', 'expires', 'max-age', 'secure', 'httponly', 'samesite']:
                                result[key] = value
                elif '=' in line:
                    # 单行单个 Cookie（每行一个）
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if key and value:
                        result[key] = value

            return result

        except Exception as e:
            CommonUtil.printLog(f"❌ 解析 Cookie 失败: {e}")
            import traceback
            CommonUtil.printLog(traceback.format_exc())
            return {}

    @staticmethod
    def parse_json_str(json_str: str, default_value: Dict = None) -> Dict[str, Any]:
        """
        解析JSON字符串

        :param json_str: JSON字符串
        :param default_value: 解析失败后的默认值
        :return: JSON对象
        """
        if CommonUtil.isNoneOrBlank(json_str):
            return default_value

        try:
            return json.loads(json_str)
        except Exception as e:
            CommonUtil.printLog(f"❌ parse_json_str失败: 错误信息: {e}, {json_str}")
            return default_value


# 配置日志
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# default_logger = logging.getLogger(__name__)

def catch_exceptions(max_retries=0, retry_interval=1, logger=None):
    """
    带重试功能的异常处理装饰器

    参数:
        max_retries: 最大重试次数,默认不充实
        retry_interval: 重试间隔(秒)
        logger: 日志记录器
    """
    if logger is None:
        from util.log_handler import DefaultCustomLog
        logger = DefaultCustomLog.get_log()

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries <= max_retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    retries += 1
                    if retries > max_retries:
                        error_msg = f"任务 {func.__name__} 重试 {max_retries} 次后失败: {str(e)}"
                        logger.error(error_msg, exc_info=True)
                        # 发送告警
                        # send_alert(error_msg)
                        break

                    wait_msg = f"任务 {func.__name__} 执行异常，{retry_interval} 秒后重试 ({retries}/{max_retries}): {str(e)}"
                    logger.warning(wait_msg, exc_info=True)
                    time.sleep(retry_interval)
            return None

        return wrapper

    return decorator


def singleton(cls):
    """
    单例装饰器,线程安全
    """
    instances = {}
    lock = threading.Lock()

    def get_instance(*args, **kwargs):
        if cls not in instances:
            with lock:
                if cls not in instances:  # 双重检查
                    instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return get_instance


if __name__ == '__main__':
    #     _process = CommonUtil.exeCmdAsync(['scrcpy', '--audio-source=output', '--audio-codec=aac', '--record', 'abc.mp4'])
    #     time.sleep(3)
    #     CommonUtil.stopProcess(_process)
    rst = CommonUtil.generate_random_numbers(10, 0, 100)
    print(rst)
    print(sorted(rst))
