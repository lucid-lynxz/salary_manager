#!/bin/bash
# 麒麟/国产系统环境一键全面检测：bash hello_test.sh 2>&1 | tee env_report.txt
# 输出较长，建议用 tee 存文件直接回传；或滚动截图全部
echo "===== 环境检测脚本开始 ====="
echo "Hello World!"

echo "----- [系统] -----"
echo "系统内核: $(uname -a)"
echo "发行版: $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2)"
echo "CPU 架构: $(uname -m)"
echo "bash 路径: $(command -v bash)"
echo "语言环境: $(echo $LANG)"

echo "----- [Python 环境] -----"
echo "python3 路径: $(command -v python3 || echo 未安装)"
echo "python3 版本: $(python3 -V 2>&1 || echo 'python3 无法运行')"
echo "pip: $(python3 -m pip -V 2>&1 | head -1 || true)"
[ -z "$(command -v pip3 2>/dev/null)" ] && echo "（若无 pip：sudo apt install python3-pip python3-venv）"
echo "venv 模块: $(python3 -c 'import venv, ensurepip; print("venv OK")' 2>&1 | tail -1)"
echo "sqlite3 模块: $(python3 -c 'import sqlite3; print("sqlite3 OK", sqlite3.sqlite_version)' 2>&1 | tail -1)"
echo "ssl 模块: $(python3 -c 'import ssl; print("ssl OK", ssl.OPENSSL_VERSION)' 2>&1 | tail -1)"

echo "----- [目录与文件系统] -----"
echo "当前目录: $(pwd)"
echo "时间: $(date)"
if command -v findmnt >/dev/null 2>&1; then
    echo "所在挂载点: $(findmnt -T "$PWD" -n -o TARGET,SOURCE,FSTYPE,OPTIONS 2>/dev/null)"
else
    echo "所在挂载点: (无 findmnt) $(df -T . 2>/dev/null | tail -1)"
fi
echo -n "目录可写性: "
if touch .env_test_w 2>/dev/null; then echo "WRITE_OK"; rm -f .env_test_w; else echo "WRITE_FAIL（只读挂载或无权限）"; fi
echo -n "脚本可执行性(noexec 检测): "
if printf '#!/bin/bash\necho ok\n' > .env_test_x 2>/dev/null && chmod +x .env_test_x 2>/dev/null && ./.env_test_x >/dev/null 2>&1; then
    echo "EXEC_OK"
else
    echo "EXEC_FAIL（该文件系统以 noexec 挂载，./start.sh 会静默失败；请把项目拷到本机硬盘，或始终用 bash start.sh 方式运行）"
fi
rm -f .env_test_x
echo "磁盘剩余空间: $(df -h . 2>/dev/null | tail -1 | awk '{print $4" 可用（已用 "$5"）"}')"

echo "----- [网络与代理] -----"
echo -n "pypi.org 连通性: "
if command -v curl >/dev/null 2>&1; then
    curl -sI --max-time 8 https://pypi.org/simple/ -o /dev/null -w "HTTP %{http_code}（%{time_total}s）\n" 2>/dev/null || echo "FAIL（无法访问 PyPI，检查网络/防火墙/代理）"
elif command -v wget >/dev/null 2>&1; then
    wget -q -T 8 -O /dev/null https://pypi.org/simple/ && echo "OK" || echo "FAIL（无法访问 PyPI）"
else
    python3 -c "import urllib.request as u; u.urlopen('https://pypi.org/simple/', timeout=8); print('OK')" 2>/dev/null || echo "FAIL（无 curl/wget 且 python 也无法连接）"
fi
echo "代理变量: http_proxy=${http_proxy:-无} https_proxy=${https_proxy:-无}"

echo "----- [端口与工具] -----"
echo "lsof: $(command -v lsof || echo 未安装（start.sh 会退回 ss 检测）)"
echo "ss: $(command -v ss || echo 未安装)"
echo "xdg-open(自动开浏览器): $(command -v xdg-open || echo 未安装（需手动访问 http://127.0.0.1:5000）)"
if command -v ss >/dev/null 2>&1; then
    echo "5000 端口占用: $(ss -ltn 2>/dev/null | grep -c ':5000 ') 个监听进程"
elif command -v lsof >/dev/null 2>&1; then
    echo "5000 端口占用: $(lsof -nP -iTCP:5000 -sTCP:LISTEN 2>/dev/null | grep -c LISTEN) 个监听进程"
fi

echo "===== 检测结束：请回传 env_report.txt 或滚动截图全部内容，等 60 秒或按 Ctrl+C 退出 ====="
sleep 60
echo "测试完成，脚本可正常执行"
