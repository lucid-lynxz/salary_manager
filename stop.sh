#!/bin/bash
# 一键停止工资单合并工具

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.app.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "未检测到运行中的服务（找不到 PID 文件）"
    exit 0
fi

PID=$(cat "$PID_FILE")
if ps -p "$PID" > /dev/null 2>&1; then
    echo "正在停止服务 (PID: $PID)..."
    kill "$PID"
    sleep 1
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "进程未退出，强制结束..."
        kill -9 "$PID" || true
    fi
    echo "服务已停止"
else
    echo "服务进程已不存在"
fi

rm -f "$PID_FILE"
