#!/bin/bash
# 一键启动工资单合并工具
# 首次运行会自动创建虚拟环境并安装依赖

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.app.pid"
PORT=5000

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

# 清理占用端口的遗留进程
if [ "$IS_WINDOWS" = "1" ]; then
    # Windows: netstat + taskkill
    PIDS_ON_PORT=$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -i LISTENING | awk '{print $5}' | sort -u || true)
    if [ -n "$PIDS_ON_PORT" ]; then
        echo "检测到端口 $PORT 被占用，正在清理遗留进程..."
        for pid in $PIDS_ON_PORT; do
            taskkill //F //PID "$pid" 2>/dev/null || true
        done
        sleep 1
    fi
else
    PIDS_ON_PORT=$(lsof -ti :$PORT 2>/dev/null || true)
    if [ -n "$PIDS_ON_PORT" ]; then
        echo "检测到端口 $PORT 被占用，正在清理遗留进程..."
        echo "$PIDS_ON_PORT" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
fi

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "服务已在运行中 (PID: $PID)，请访问 http://127.0.0.1:$PORT"
        echo "如需重启，请先运行 ./stop.sh"
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "正在创建虚拟环境..."
    "$PYTHON_CMD" -m venv venv
fi

# 安装依赖
echo "正在安装/更新依赖..."
"$VENV_PYTHON" -m pip install -q -r requirements.txt

# 启动服务（后台运行）
echo "正在启动服务..."
"$VENV_PYTHON" app.py > app.log 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

# 等待服务启动，通过端口监听状态判断（比 ps -p 更可靠，避免 Flask debug reloader PID 不匹配问题）
sleep 3
if [ "$IS_WINDOWS" = "1" ]; then
    PORT_LISTENING=$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -i LISTENING || true)
else
    PORT_LISTENING=$(lsof -i :$PORT 2>/dev/null || true)
fi
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
    rm -f "$PID_FILE"
    exit 1
fi
