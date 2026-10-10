#!/bin/bash
# 一键启动工资单合并工具
# 首次运行会自动创建虚拟环境并安装依赖

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.app.pid"
PORT=5000

echo "===== 工资单合并工具 启动脚本 ====="

# 检测操作系统
OS_TYPE="$(uname -s)"
case "$OS_TYPE" in
    CYGWIN*|MINGW*|MSYS*) IS_WINDOWS=1 ;;
    *) IS_WINDOWS=0 ;;
esac

# 跨平台：venv 中的 python 可执行文件路径与系统 python 命令
if [ "$IS_WINDOWS" = "1" ]; then
    VENV_PYTHON="$SCRIPT_DIR/venv/Scripts/python.exe"
    PYTHON_CMD="python"
else
    VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
    PYTHON_CMD="python3"
fi

# 查询占用端口的 PID：优先 lsof，退回 ss（银河麒麟等系统可能未装 lsof）
# lsof 必须限定 -sTCP:LISTEN，否则会把"连到远端 5000 端口"的客户端进程也算进来
port_pids() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
    elif command -v ss >/dev/null 2>&1; then
        # 只从含 pid= 的行取值：非本用户进程或内核不支持 -p 时 ss 不输出 pid=，
        # 用 sed 会把整行当成"pid"交给 kill，导致清理静默失败；一行多个 pid= 时也要全部取到
        ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9][0-9]*' | cut -d= -f2 | sort -u || true
    fi
}

# 端口是否处于监听：输出非空表示监听中；输出 unknown 表示缺少检测工具无法判断
port_listening() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
    elif command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | grep ":$PORT " || true
    else
        echo "unknown"
    fi
}

# 进程是否存活：Windows 下 Git Bash 的 MSYS ps 与 Windows PID 口径不一致，
# 容易把已死进程误判为"仍在运行"而提示访问一个打不开的页面，改用 tasklist 按 PID 过滤
pid_alive() {
    if [ "$IS_WINDOWS" = "1" ]; then
        tasklist //FI "PID eq $1" //NH 2>/dev/null | grep -q " $1 "
    else
        ps -p "$1" > /dev/null 2>&1
    fi
}

# 清理占用端口的遗留进程
if [ "$IS_WINDOWS" = "1" ]; then
    # Windows: netstat + taskkill（/T 递归结束进程树：Flask debug 模式下真正监听端口的是 reloader 子进程，
    # /F 属硬终止不会触发 werkzeug 的 SIGTERM 清理逻辑，只杀父进程会留下占用 5000 的子进程）
    PIDS_ON_PORT=$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -i LISTENING | awk '{print $5}' | sort -u || true)
    if [ -n "$PIDS_ON_PORT" ]; then
        echo "检测到端口 $PORT 被占用，正在清理遗留进程..."
        for pid in $PIDS_ON_PORT; do
            taskkill //F //T //PID "$pid" 2>/dev/null || true
        done
        sleep 1
    fi
else
    PIDS_ON_PORT=$(port_pids)
    if [ -n "$PIDS_ON_PORT" ]; then
        echo "检测到端口 $PORT 被占用，正在清理遗留进程..."
        echo "$PIDS_ON_PORT" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
fi

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    # tr -d '\r'：PID 文件若被 Windows 编辑器改过会带 CRLF，残留 \r 会让存活判断与 kill 失效
    PID=$(tr -d '\r' < "$PID_FILE")
    if pid_alive "$PID"; then
        echo "服务已在运行中 (PID: $PID)，请访问 http://127.0.0.1:$PORT"
        echo "如需重启，请先运行 ./stop.sh"
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# Linux 环境检查：python3 与 venv 模块缺失时给出明确安装指引
if [ "$IS_WINDOWS" = "0" ]; then
    if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
        echo "错误：未检测到 $PYTHON_CMD，请先安装 Python 3 环境"
        echo "  银河麒麟 / UOS / Ubuntu / Deepin: sudo apt install python3 python3-venv python3-pip"
        echo "  CentOS / openEuler:              sudo yum install python3 python3-pip"
        exit 1
    fi
    echo "Python 环境: $($PYTHON_CMD --version 2>&1)"
    if [ ! -d "venv" ] && ! "$PYTHON_CMD" -c "import venv, ensurepip" >/dev/null 2>&1; then
        echo "错误：缺少 python3-venv 模块，无法创建虚拟环境"
        echo "  银河麒麟 / UOS / Ubuntu / Deepin: sudo apt install python3-venv"
        exit 1
    fi
fi

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "正在创建虚拟环境..."
    "$PYTHON_CMD" -m venv venv
fi

# 安装依赖
echo "正在安装/更新依赖..."
if ! "$VENV_PYTHON" -m pip install -q -r requirements.txt; then
    echo "错误：依赖安装失败，请检查网络连接后重新运行本脚本"
    exit 1
fi

# 启动服务（后台运行）
# PYTHONUNBUFFERED: stdout 重定向到文件时默认是块缓冲，不加该变量 Flask 启动横幅会滞留缓冲区，
# 导致 app.log 看起来"没有日志"，排查启动失败时无信息可看
echo "正在启动服务..."
PYTHONUNBUFFERED=1 "$VENV_PYTHON" app.py > app.log 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

# 等待服务启动，通过端口监听状态判断（比 ps -p 更可靠，避免 Flask debug reloader PID 不匹配问题）
# 首次导入 pandas/flask 较慢，轮询最多 10 秒，避免误判为启动失败
WAITED=0
PORT_LISTENING=""
while [ "$WAITED" -lt 10 ]; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "$IS_WINDOWS" = "1" ]; then
        PORT_LISTENING=$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -i LISTENING || true)
    else
        PORT_LISTENING=$(port_listening)
        # 缺少 lsof/ss 时退回进程存活检查
        if [ "$PORT_LISTENING" = "unknown" ] && pid_alive "$PID"; then
            PORT_LISTENING="alive"
        fi
    fi
    # 注意：此处用 if 而非 "[ ... ] && break"，避免 set -e 下测试失败时脚本直接退出
    if [ -n "$PORT_LISTENING" ]; then
        break
    fi
done
if [ -n "$PORT_LISTENING" ]; then
    URL="http://127.0.0.1:$PORT"
    echo "启动成功！正在打开浏览器: $URL"
    echo "日志文件: $SCRIPT_DIR/app.log"
    echo "停止服务请运行: ./stop.sh"

    # 根据系统自动打开浏览器
    case "$OS_TYPE" in
        Darwin*) open "$URL" ;;
        Linux*)  xdg-open "$URL" 2>/dev/null || echo "请手动在浏览器打开 $URL" ;;
        CYGWIN*|MINGW*|MSYS*) cmd //c start "" "$URL" ;;
        *) echo "请手动在浏览器打开 $URL" ;;
    esac
else
    echo "启动失败，请查看日志: $SCRIPT_DIR/app.log"
    tail -n 20 "$SCRIPT_DIR/app.log" 2>/dev/null || true
    rm -f "$PID_FILE"
    exit 1
fi
