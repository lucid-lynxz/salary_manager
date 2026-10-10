#!/bin/bash
# 最小测试脚本：验证 shell 能否执行 + 输出环境信息，方便截图排查
echo "===== HELLO 测试脚本开始 ====="
echo "Hello World!"
echo "系统内核: $(uname -a)"
echo "发行版: $(cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'"' -f2)"
echo "bash 路径: $(command -v bash)"
echo "python3: $(command -v python3 || echo 未安装)"
echo "--- Python 环境检测 ---"
echo "python3 版本: $(python3 -V 2>&1 || echo 'python3 无法运行')"
echo "pip: $(python3 -m pip -V 2>&1 | head -1 || true)"
[ -z "$(command -v pip3 2>/dev/null)" ] && echo "（若无 pip：sudo apt install python3-pip python3-venv）"
echo "venv 模块: $(python3 -c 'import venv, ensurepip; print("venv OK")' 2>&1 | tail -1)"
echo "当前目录: $(pwd)"
echo "时间: $(date)"
echo "===== 保持 60 秒，请截图后按 Ctrl+C 或等待自动退出 ====="
sleep 60
echo "测试完成，脚本可正常执行"
