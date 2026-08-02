# ZMQ 客户端验证指南

## ⚠️ 重要发现

**macOS 容器平台的端口转发对 ZMQ 支持有限制**：
- ✅ 容器内部 ZMQ 通信正常
- ❌ 主机 → 容器 ZMQ 通信：请求可达，响应无法返回

## 验证结果

```bash
# 容器内测试 - ✅ 成功
./scripts/container_run_client.sh
# 输出: Response: {'result': {'pong': True, ...}}

# 主机测试 - ❌ 响应超时
poetry run python scripts/test_zmq_client.py
# 请求可达（服务器 processed=1），但响应无法返回
```

## 解决方案

### 方案 1：客户端运行在容器内（推荐）

将 `bt_studio` 也放入容器，使用 Docker Compose 或自定义脚本：

```yaml
# docker-compose.yml
services:
  xtp-service:
    build: .
    ports:
      - "5570:5570"
    networks:
      - xtp-net

  bt-studio:
    build: ../bt_studio
    depends_on:
      - xtp-service
    environment:
      - ZMQ_ENDPOINT=tcp://xtp-service:5570
    networks:
      - xtp-net
```

### 方案 2：使用 UNIX Socket

修改服务端使用 UNIX socket 而非 TCP，通过 bind mount 共享：

```python
# 服务端
frontend_url = "ipc:///workspace/xtp.ipc"

# 客户端
client = XtpClient(endpoint="ipc:///Users/hengxinliu/startup/bt_playgroud/xtp.ipc")
```

### 方案 3：HTTP 桥接

添加 HTTP API 层桥接 ZMQ 服务：

```python
# 在容器内运行 HTTP 服务
from fastapi import FastAPI
from bt_studio.plugins.zmq_client import XtpClient

app = FastAPI()
client = XtpClient("tcp://127.0.0.1:5570")

@app.post("/api/{method}")
async def call(method: str, params: dict = None):
    return await client.call(method, params or {})
```

主机通过 HTTP 访问：`curl http://localhost:8000/api/ping`

## 快速测试

### 当前测试状态

| 测试 | 结果 |
|------|------|
| 容器内客户端 | ✅ 正常 |
| 主机 → 容器 TCP | ❌ 响应超时 |
| 端口可达性 | ✅ 端口已映射 |
| 请求到达 | ✅ 服务器已处理 |

### 运行测试

```bash
# 服务已在运行（container_run_auto.sh 启动）

# 测试 1: 容器内客户端 ✅
./scripts/container_run_client.sh

# 测试 2: 主机客户端 ❌
poetry run python scripts/test_zmq_client.py
```

## 服务状态检查

```bash
# 查看运行中的容器
container list

# 查看服务日志
container logs xtp-service-auto

# 查看统计（processed 表示处理请求数）
container logs xtp-service-auto | grep "Stats:"
```

## 架构说明

```
┌─────────────────────┐         ┌──────────────────────┐
│  容器 (AMD64)        │         │  主机 (macOS ARM)    │
├─────────────────────┤         ├──────────────────────┤
│ zmq_client.py       │         │  zmq_client.py       │
│ (容器内)             │         │  (主机上)             │
│ ✓ 正常              │  ✗      │  ✗ 响应超时          │
└─────────────────────┘  端口转发└──────────────────────┘
                              │
                              ▼
                    ┌──────────────────────┐
                    │  run_server.py       │
                    │  tcp://0.0.0.0:5570  │
                    │  ROUTER/DEALER 架构   │
                    └──────────────────────┘
```

## 下一步

1. 如果需要主机访问：选择方案 2（UNIX Socket）或方案 3（HTTP 桥接）
2. 如果只需容器内使用：当前架构已满足需求
