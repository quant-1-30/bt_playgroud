#!/bin/bash
# macOS 容器快速启动脚本
# 用途: 快速启动容器，挂载宿主机目录，映射 ZMQ 端口

set -e

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 默认配置
IMAGE_NAME="${IMAGE_NAME:-ubuntu:22.04}"
CONTAINER_NAME="${CONTAINER_NAME:-xtp-service}"
WORKSPACE="${WORKSPACE:-/workspace}"

# ZMQ 端口（来自 config.py 默认配置）
ZMQ_PORT="${ZMQ_PORT:-5570}"
ZMQ_BACKEND_PORT="${ZMQ_BACKEND_PORT:-5571}"

echo "========================================="
echo "XTP Service 容器快速启动"
echo "========================================="
echo ""
echo "配置信息:"
echo "  镜像:          $IMAGE_NAME"
echo "  容器名:        $CONTAINER_NAME"
echo "  项目目录:      $PROJECT_DIR"
echo "  容器工作目录:  $WORKSPACE"
echo "  ZMQ 端口:      $ZMQ_PORT"
echo "  ZMQ 后端端口:  $ZMQ_BACKEND_PORT"
echo ""

# 检查容器是否已存在
if container list 2>/dev/null | grep -q "$CONTAINER_NAME"; then
    echo "⚠ 容器 '$CONTAINER_NAME' 已存在"
    if [[ -t 0 ]]; then
        read -p "是否删除旧容器并重新创建? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "删除旧容器..."
            container delete -f "$CONTAINER_NAME" 2>/dev/null || true
        else
            echo "启动现有容器..."
            container exec -it "$CONTAINER_NAME" /bin/bash
            exit 0
        fi
    else
        echo "删除旧容器..."
        container delete -f "$CONTAINER_NAME" 2>/dev/null || true
    fi
fi

echo "启动新容器..."
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
        echo '容器已启动'
        echo '========================================='
        echo ''
        echo '首次运行需要配置环境，请执行:'
        echo '  bash scripts/container_setup_env.sh'
        echo ''
        echo '然后安装项目依赖:'
        echo '  poetry install'
        echo ''
        echo '运行服务器:'
        echo '  python3 scripts/run_server.py'
        echo ''
        /bin/bash
    "

echo ""
echo "提示: 使用 'container exec -it $CONTAINER_NAME /bin/bash' 重新进入容器"
