# macOS 原生容器使用指南

## ⚠️ 平台要求

**XTP 服务必须在 amd64 (x86_64) 平台上运行**

- XTP 的 `.so` 库是 x86 架构，不支持 ARM
- Apple Mac (ARM 架构) 必须使用 `--platform linux/amd64` 参数
- 脚本已自动配置，无需手动指定

## 前置条件

确保 macOS 容器平台已安装：

```bash
# 检查容器命令
container --version

# 查看可用镜像
container image list

# 查看运行中的容器
container list
```

## 快速开始

### 方式一：一键自动启动（推荐）

```bash
cd /Users/hengxinliu/startup/bt_playgroud
./scripts/container_run_auto.sh
```

这会自动完成：
1. ✅ 启动 ubuntu 容器
2. ✅ 安装 Python 3.11 + Poetry
3. ✅ 安装项目依赖
4. ✅ 启动 ZMQ 服务器

### 方式二：手动步骤（调试用）

```bash
# 1. 启动容器
./scripts/container_run.sh

# 2. 在容器内配置环境
bash scripts/container_setup_env.sh

# 3. 安装依赖
export PATH="/root/.local/bin:$PATH"
poetry install

# 4. 运行服务器
python3 scripts/run_server.py
```

## 端口说明

| 端口 | 用途 | 默认值 |
|------|------|--------|
| ZMQ_PORT | ZMQ 主端口（RPC 通信） | 5570 |
| ZMQ_BACKEND_PORT | ZMQ 后端端口 | 5571 |

## 自定义配置

```bash
# 自定义镜像
IMAGE_NAME=ubuntu:22.04 ./scripts/container_run_auto.sh

# 自定义端口
ZMQ_PORT=6666 ZMQ_BACKEND_PORT=6667 ./scripts/container_run_auto.sh

# 自定义容器名
CONTAINER_NAME=my-xtp-service ./scripts/container_run_auto.sh
```

## 常用容器命令

```bash
# 查看运行中的容器
container list
container ls

# 查看所有镜像
container image list
container image ls

# 重新进入容器
container exec -it xtp-service /bin/bash

# 停止容器
container stop xtp-service

# 删除容器
container delete xtp-service
container rm xtp-service

# 查看容器日志
container logs xtp-service

# 实时查看日志
container logs -f xtp-service

# 复制文件到容器
container copy src.txt xtp-service:/workspace/

# 从容器复制文件
container copy xtp-service:/workspace/output.txt ./

# 查看容器详情
container inspect xtp-service

# 查看容器资源使用
container stats xtp-service

# 清理所有停止的容器
container prune
```

## 镜像命令

```bash
# 构建镜像
container image build -t myimage .

# 拉取镜像（从注册表）
container registry login

# 导出镜像
container export myimage > myimage.tar

# 删除镜像
container image delete ubuntu:22.04
```

## 网络命令

```bash
# 查看网络
container network list

# 创建网络
container network create mynet

# 连接容器到网络
container network connect mynet xtp-service
```

## 测试 ZMQ 连接

从宿主机测试 ZMQ 端口是否可访问：

```bash
# 检查端口监听
lsof -i :5570

# 使用 nc 测试连接
echo "test" | nc localhost 5570

# 使用 telnet 测试
telnet localhost 5570
```

## 故障排查

### 容器启动失败
```bash
# 查看详细错误
container logs xtp-service-auto

# 重新创建容器
container delete -f xtp-service-auto
./scripts/container_run_auto.sh
```

### Poetry 安装失败
```bash
# 手动在容器内安装
container exec -it xtp-service-auto /bin/bash
curl -sSL https://install.python-poetry.org | python3 -
```

### 依赖安装失败
```bash
# 清理缓存重试
container exec -it xtp-service-auto /bin/bash
poetry cache clear pypi --all
poetry install
```

### 端口冲突
```bash
# 检查端口占用
lsof -i :5570
lsof -i :5571

# 使用其他端口
ZMQ_PORT=6666 ZMQ_BACKEND_PORT=6667 ./scripts/container_run_auto.sh
```

## 对比 Docker 命令

| Docker | macOS Container |
|--------|-----------------|
| `docker run` | `container run` |
| `docker ps` | `container list` |
| `docker exec` | `container exec` |
| `docker logs` | `container logs` |
| `docker stop` | `container stop` |
| `docker rm` | `container delete` |
| `docker images` | `container image list` |
| `-v .:/workspace` | `--mount type=bind,source=.,target=/workspace` |
| `-p 5570:5570` | `--publish 5570:5570` |
