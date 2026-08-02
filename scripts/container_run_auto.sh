#!/bin/bash
# macOS 容器一键启动并运行脚本
# 用途: 启动容器，自动配置环境，安装依赖，运行服务器

set -e

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 默认配置
IMAGE_NAME="${IMAGE_NAME:-ubuntu:22.04}"
CONTAINER_NAME="${CONTAINER_NAME:-xtp-service-auto}"
WORKSPACE="${WORKSPACE:-/workspace}"

# ZMQ 端口
ZMQ_PORT="${ZMQ_PORT:-5570}"
ZMQ_BACKEND_PORT="${ZMQ_BACKEND_PORT:-5571}"

echo "========================================="
echo "XTP Service 一键启动（自动配置）"
echo "========================================="
echo ""
echo "配置信息:"
echo "  镜像:          $IMAGE_NAME"
echo "  容器名:        $CONTAINER_NAME"
echo "  项目目录:      $PROJECT_DIR"
echo "  ZMQ 端口:      $ZMQ_PORT (主机) -> $ZMQ_PORT (容器)"
echo "  ZMQ 后端端口:  $ZMQ_BACKEND_PORT (主机) -> $ZMQ_BACKEND_PORT (容器)"
echo ""

# 清理旧容器（如果存在）
if container list 2>/dev/null | grep -q "$CONTAINER_NAME"; then
    echo "删除旧容器 '$CONTAINER_NAME'..."
    container delete -f "$CONTAINER_NAME" 2>/dev/null || true
fi

# 启动新容器并自动配置
echo "启动容器并自动配置环境..."
echo ""

# 检测是否在交互终端
INTERACTIVE_FLAG=""
if [[ -t 0 ]]; then
    INTERACTIVE_FLAG="-it"
fi

# macOS 容器启动命令
# 强制使用 amd64 平台（XTP .so 库是 x86 架构）
container run $INTERACTIVE_FLAG \
    --platform linux/amd64 \
    --name "$CONTAINER_NAME" \
    --mount type=bind,source="$PROJECT_DIR",target="$WORKSPACE" \
    --publish "$ZMQ_PORT:$ZMQ_PORT" \
    --publish "$ZMQ_BACKEND_PORT:$ZMQ_BACKEND_PORT" \
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
        echo '步骤 3: 启动服务器'
        echo '========================================='
        echo ''
        echo 'ZMQ 服务将在以下端口监听:'
        echo '  - 主端口: $ZMQ_PORT'
        echo '  - 后端端口: $ZMQ_BACKEND_PORT'
        echo ''
        echo '按 Ctrl+C 停止服务器'
        echo ''
        python3 scripts/run_server.py
    "

echo ""
echo "服务器已停止"
echo "使用 'container start $CONTAINER_NAME' 重新启动容器"
echo "使用 'container exec -it $CONTAINER_NAME /bin/bash' 进入容器"
