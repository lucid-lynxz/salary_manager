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
# lsof 必须限定 -sTCP:LISTEN，否则会把"连到远端 5000 端口"的客户端进程也一并 kill 掉
port_pids() {
    if [ "$IS_WINDOWS" = "1" ]; then
        netstat -ano 2>/dev/null | grep ":$PORT " | grep -i LISTENING | awk '{print $5}' | sort -u || true
    elif command -v lsof >/dev/null 2>&1; then
        lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
    elif command -v ss >/dev/null 2>&1; then
        # 只从含 pid= 的行取值：非本用户进程或内核不支持 -p 时 ss 不输出 pid=，
        # 用 sed 会把整行当成"pid"交给 kill，导致停止静默失败
        ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9][0-9]*' | cut -d= -f2 | sort -u || true
    fi
}

# 进程是否存活：Windows 下 Git Bash 的 MSYS ps 与 Windows PID 口径不一致，改用 tasklist 按 PID 过滤
pid_alive() {
    if [ "$IS_WINDOWS" = "1" ]; then
        tasklist //FI "PID eq $1" //NH 2>/dev/null | grep -q " $1 "
    else
        ps -p "$1" > /dev/null 2>&1
    fi
}

stop_pid() {
    local pid="$1"
    if [ "$IS_WINDOWS" = "1" ]; then
        # /T 递归结束进程树：Flask debug 模式下真正监听端口的是 reloader 子进程，
        # /F 属硬终止不会触发 werkzeug 的 SIGTERM 清理逻辑，只杀父进程会留下占用端口的子进程
        if ! taskkill //F //T //PID "$pid" >/dev/null 2>&1; then
            echo "停止进程 $pid 失败：可能权限不足（目标进程以管理员或其他用户运行），请以管理员身份重试"
            return 1
        fi
    else
        kill "$pid" 2>/dev/null || true
        sleep 1
        if pid_alive "$pid"; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi
    return 0
}

STOPPED=0

# 优先按 PID 文件停止（start.sh 启动的进程）
if [ -f "$PID_FILE" ]; then
    # tr -d '\r'：PID 文件若被 Windows 编辑器改过会带 CRLF，残留 \r 会让存活判断与 kill 失效
    PID=$(tr -d '\r' < "$PID_FILE")
    if pid_alive "$PID"; then
        echo "正在停止服务 (PID: $PID)..."
        stop_pid "$PID" || true
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
            stop_pid "$pid" || true
        done
        STOPPED=1
    fi
fi

if [ "$STOPPED" = "1" ]; then
    # 停止后复查端口，确认真的释放
    sleep 1
    LEFT=$(port_pids)
    if [ -n "$LEFT" ]; then
        echo "警告：端口 $PORT 仍被进程 $(echo "$LEFT" | tr '\n' ' ') 占用，停止未成功（通常是权限不足）"
        exit 1
    fi
    echo "服务已停止"
else
    echo "未检测到运行中的服务（PID 文件与端口 $PORT 均无记录）"
fi
