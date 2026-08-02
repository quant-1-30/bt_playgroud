#!/bin/bash
# macOS 容器环境配置脚本
# 用途: 在 ubuntu 容器中安装 Python 3.11 和 Poetry
# 使用方法: 在容器内执行此脚本

set -e

echo "========================================="
echo "XTP Service 容器环境配置"
echo "========================================="
echo ""

# 检测是否在容器内
if [ -f /.dockerenv ] || grep -q container /proc/1/cgroup 2>/dev/null; then
    echo "✓ 检测到运行在容器内"
else
    echo "⚠ 警告: 可能不在容器内运行"
fi

# 检测 Linux 架构
ARCH=$(uname -m)
echo "系统架构: $ARCH"

# XTP .so 库是 x86 架构，必须运行在 amd64/x86_64 平台
if [[ "$ARCH" == "x86_64" ]]; then
    PYTHON_VERSION="python3.11"
    echo "✓ x86_64/amd64 平台（XTP 兼容）"
elif [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
    echo "❌ 错误: XTP .so 库是 x86 架构，不支持 ARM 平台"
    echo "   请使用 --platform linux/amd64 启动容器"
    exit 1
else
    echo "❌ 未知架构: $ARCH"
    exit 1
fi

echo ""
echo "步骤 1/5: 更新包列表..."
apt-get update

echo ""
echo "步骤 2/5: 安装 Python 和相关工具..."
apt-get install -y \
    ${PYTHON_VERSION} \
    python3-pip \
    python3-venv \
    git \
    curl \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev

echo ""
echo "步骤 3/5: 安装 Poetry..."
# 使用官方安装脚本安装 Poetry
curl -sSL https://install.python-poetry.org | python3 -

# 确保 poetry 在 PATH 中
export PATH="/root/.local/bin:$PATH"

echo ""
echo "步骤 4/5: 验证安装..."
echo "Python 版本:"
${PYTHON_VERSION} --version

echo ""
echo "Poetry 版本:"
poetry --version

echo ""
echo "步骤 5 5: 配置 Poetry..."
# 配置 Poetry 不创建虚拟环境（适合容器环境）
poetry config virtualenvs.create false
poetry config virtualenvs.in-project false

echo "Poetry 配置:"
poetry config --list

echo ""
echo "========================================="
echo "✓ 环境配置完成!"
echo "========================================="
echo ""
echo "下一步操作:"
echo "  cd /workspace"
echo "  poetry install"
echo "  python3 scripts/run_server.py"
echo ""
