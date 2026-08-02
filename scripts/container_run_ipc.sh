#!/bin/bash
# macOS 容器 IPC 模式启动脚本
# 用途: 使用 UNIX Socket 实现主机与容器通信，避免 TCP 端口转发限制

set -e

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 默认配置
IMAGE_NAME="${IMAGE_NAME:-ubuntu:22.04}"
CONTAINER_NAME="${CONTAINER_NAME:-xtp-service-ipc}"
WORKSPACE="${WORKSPACE:-/workspace}"

# IPC Socket 路径
# 使用固定的 .ipc 目录来共享 socket
IPC_DIR="${IPC_DIR:-/workspace/.ipc}"
IPC_SOCKET_PATH="${IPC_SOCKET_PATH:-/workspace/.ipc/xtp.ipc}"
# 主机路径（用于 bind mount）
HOST_IPC_DIR="${HOST_IPC_DIR:-$PROJECT_DIR/.ipc}"

echo "========================================="
echo "XTP Service IPC 模式启动"
echo "========================================="
echo ""
echo "配置信息:"
echo "  镜像:          $IMAGE_NAME"
echo "  容器名:        $CONTAINER_NAME"
echo "  项目目录:      $PROJECT_DIR"
echo "  IPC Socket:    $HOST_IPC_DIR/xtp.ipc (主机)"
echo "                -> $IPC_SOCKET_PATH (容器)"
echo ""

# 创建 IPC 目录
echo "创建 IPC Socket 目录..."
mkdir -p "$HOST_IPC_DIR"
# 清理旧的 socket 文件
rm -f "$HOST_IPC_DIR/xtp.ipc" "$HOST_IPC_DIR/xtp.ipc.backend"

# 清理旧容器（如果存在）
if container list 2>/dev/null | grep -q "$CONTAINER_NAME"; then
    echo "删除旧容器 '$CONTAINER_NAME'..."
    container delete -f "$CONTAINER_NAME" 2>/dev/null || true
fi

# 检测是否在交互终端
INTERACTIVE_FLAG=""
if [[ -t 0 ]]; then
    INTERACTIVE_FLAG="-it"
fi

# macOS 容器启动命令 - 使用 bind mount 共享 IPC 目录
echo "启动容器并配置环境..."
echo ""

container run $INTERACTIVE_FLAG \
    --platform linux/amd64 \
    --name "$CONTAINER_NAME" \
    --mount type=bind,source="$PROJECT_DIR",target="$WORKSPACE" \
    --mount type=bind,source="$HOST_IPC_DIR",target="/workspace/.ipc" \
    --workdir "$WORKSPACE" \
    "$IMAGE_NAME" \
    /bin/bash -c "
        echo '========================================='
        echo '步骤 1: 配置环境'
        echo '========================================='
        bash scripts/container_setup_env.sh

        echo ''
        echo '========================================='
        echo '步骤 2: 安装项目依赖'
        echo '========================================='
        export PATH=\"/root/.local/bin:\$PATH\"
        poetry install

        echo ''
        echo '========================================='
        echo '步骤 3: 启动服务器 (IPC 模式)'
        echo '========================================='
        echo ''
        echo 'ZMQ 服务将使用 IPC Socket:'
        echo '  - Socket: $IPC_SOCKET_PATH'
        echo '  - 主机可访问: $HOST_IPC_DIR/xtp.ipc'
        echo ''
        echo '按 Ctrl+C 停止服务器'
        echo ''
        export ZMQ_IPC_PATH='$IPC_SOCKET_PATH'
        python3 scripts/run_server.py
    "

echo ""
echo "服务器已停止"
echo "使用 'container start $CONTAINER_NAME' 重新启动容器"
echo "使用 'container exec $CONTAINER_NAME /bin/bash' 进入容器"
