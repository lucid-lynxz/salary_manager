#!/bin/bash
# 一键停止工资单合并工具

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.app.pid"
PORT=5000

OS_TYPE="$(uname -s)"
case "$OS_TYPE" in
    CYGWIN*|MINGW*|MSYS*) IS_WINDOWS=1 ;;
    *) IS_WINDOWS=0 ;;
esac

# 查询监听本端口的 PID：Windows 用 netstat；Linux 优先 lsof，退回 ss
port_pids() {
    if [ "$IS_WINDOWS" = "1" ]; then
        netstat -ano 2>/dev/null | grep ":$PORT " | grep -i LISTENING | awk '{print $5}' | sort -u || true
    elif command -v lsof >/dev/null 2>&1; then
        lsof -ti :"$PORT" 2>/dev/null || true
    elif command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null | grep ":$PORT " | sed 's/.*pid=\([0-9]*\).*/\1/' | sort -u || true
    fi
}

stop_pid() {
    local pid="$1"
    if [ "$IS_WINDOWS" = "1" ]; then
        taskkill //F //PID "$pid" 2>/dev/null || true
    else
        kill "$pid" 2>/dev/null || true
        sleep 1
        if ps -p "$pid" > /dev/null 2>&1; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi
}

STOPPED=0

# 优先按 PID 文件停止（start.sh 启动的进程）
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "正在停止服务 (PID: $PID)..."
        stop_pid "$PID"
        STOPPED=1
    else
        echo "PID 文件中的进程 (PID: $PID) 已不存在"
    fi
    rm -f "$PID_FILE"
fi

# 无 PID 文件或进程已失效时，按端口兜底查找（如桌面应用直接启动的实例）
if [ "$STOPPED" = "0" ]; then
    PIDS=$(port_pids)
    if [ -n "$PIDS" ]; then
        echo "未找到 PID 文件，检测到端口 $PORT 监听进程: $(echo "$PIDS" | tr '\n' ' ')，正在停止..."
        for pid in $PIDS; do
            stop_pid "$pid"
        done
        STOPPED=1
    fi
fi

if [ "$STOPPED" = "1" ]; then
    echo "服务已停止"
else
    echo "未检测到运行中的服务（PID 文件与端口 $PORT 均无记录）"
fi
