# IPC 通信方案分析报告

## 背景

在 macOS 容器平台上尝试使用 IPC (UNIX Socket) 实现主机与容器之间的 ZMQ 通信，以解决 TCP 端口转发的限制问题。

## 实现概述

### 1. 服务端配置

**`xtp_service/config.py`** - 新增 `ipc_path` 配置字段：

```python
@dataclass
class ZmqServerConfig:
    host: str = "0.0.0.0"
    port: int = 5570
    backend_port: int = 5571
    max_workers: int = 8
    rate_limit_per_minute: int = 0
    ipc_path: str = ""  # IPC socket 路径，非空时使用 IPC 而非 TCP
```

**`xtp_service/transport/zmq_server.py`** - 支持 IPC URL：

```python
def __init__(self, cfg: ZmqServerConfig) -> None:
    # 支持 IPC 或 TCP
    if cfg.ipc_path:
        self.frontend_url = f"ipc://{cfg.ipc_path}"
        self.backend_url = f"ipc://{cfg.ipc_path}.backend"
    else:
        self.frontend_url = f"tcp://{cfg.host}:{cfg.port}"
        self.backend_url = f"tcp://{cfg.host}:{cfg.backend_port}"
```

### 2. 容器启动脚本

**`scripts/container_run_ipc.sh`** - IPC 模式启动：

- 创建 `.ipc` 目录用于共享 socket
- 使用 bind mount: `--mount type=bind,source="$HOST_IPC_DIR",target="/workspace/.ipc"`
- 设置环境变量: `ZMQ_IPC_PATH=/workspace/.ipc/xtp.ipc`

### 3. 主机端客户端测试

**`scripts/test_zmq_ipc_client.py`** - 主机端 IPC 客户端测试脚本

## 测试结果

| 测试场景 | 结果 | 说明 |
|---------|------|------|
| 容器内客户端 | ✅ 成功 | `{'result': {'pong': True, 'trader_started': False, 'quote_started': False}}` |
| 主机 → IPC Socket | ❌ 失败 | 客户端挂起，连接超时 |

### 容器内测试日志

```bash
container exec xtp-service-ipc python3 scripts/test_zmq_ipc_client.py
```

输出：
```
========================================
ZMQ IPC 客户端测试
========================================
Socket 路径: /workspace/.ipc/xtp.ipc

连接到 ZMQ IPC 服务...
✓ 已连接

测试 ping...
✓ ping 响应: {'pong': True, 'trader_started': False, 'quote_started': False}

========================================
✓ 所有测试通过
========================================
```

服务端日志确认：
```
INFO - ZMQ server listening on ipc:///workspace/.ipc/xtp.ipc with 8 workers
```

Socket 文件成功创建：
- 主机可见：`.ipc/xtp.ipc` 和 `.ipc/xtp.ipc.backend`
- 容器内：`/workspace/.ipc/xtp.ipc`

## 问题分析：平台兼容性限制

### 根本原因

Linux 容器创建的 UNIX socket 与 macOS 的 socket **不兼容**：

| 属性 | Linux Socket | macOS Socket |
|------|-------------|--------------|
| 格式 | Linux 特有 | macOS 特有 |
| 可跨平台访问 | ❌ 否 | ❌ 否 |
| Bind mount 桥接 | ❌ 无效 | ❌ 无效 |

### 为什么主机无法连接

1. **Socket 格式差异**：Linux 和 macOS 的 UNIX socket 实现不同
2. **Bind mount 的限制**：bind mount 只能共享文件，不能转换 socket 格式
3. **ZMQ 验证失败**：macOS ZMQ 无法识别 Linux socket 文件格式

## 可行方案

### 方案 1：客户端运行在容器内 ✅ (已验证)

**实现方式**：将客户端代码放入容器，通过 `container exec` 运行

```bash
container exec xtp-service-ipc python3 /path/to/client.py
```

**优点**：
- 已验证可行
- 无需修改代码
- 性能最佳

**缺点**：
- 客户端必须在容器内运行
- 主机需要通过 `container exec` 间接调用

### 方案 2：使用 Docker Desktop

**实现方式**：Docker Desktop 对跨平台 socket 支持更好

```yaml
# docker-compose.yml
services:
  xtp-service:
    image: xtp-service:latest
    volumes:
      - ./xtp.ipc:/workspace/xtp.ipc
```

**优点**：
- 可能解决跨平台问题
- 更好的容器隔离

**缺点**：
- 需要安装 Docker Desktop
- 未验证可行性

### 方案 3：HTTP API 桥接 (推荐用于主机访问)

**实现方式**：在容器内添加 HTTP API 层，主机通过 HTTP 访问

**架构**：
```
主机 → HTTP → 容器内 HTTP Server → ZMQ IPC → XTP Service
```

**优点**：
- 完全跨平台兼容
- 主机可直接调用
- 支持远程访问

**缺点**：
- 需要额外实现 HTTP API
- 增加一层代理开销

### 方案 4：TCP + socat 端口转发

**实现方式**：在容器内使用 socat 将 TCP 转发到 IPC

```bash
# 容器内运行
socat TCP-LISTEN:5570,fork IPC:/workspace/.ipc/xtp.ipc
```

**优点**：
- 主机通过 TCP 连接
- 容器内使用 IPC

**缺点**：
- 依赖 macOS 容器平台的 TCP 转发能力
- 未验证可行性

## 推荐方案

| 场景 | 推荐方案 |
|------|----------|
| 客户端可在容器内运行 | **方案 1**：container exec |
| 主机需直接访问 | **方案 3**：HTTP API 桥接 |
| 可迁移到 Docker | **方案 2**：Docker Desktop |

## 文件清单

| 文件 | 状态 | 说明 |
|------|------|------|
| `xtp_service/config.py` | 已修改 | 添加 ipc_path 配置 |
| `xtp_service/transport/zmq_server.py` | 已修改 | 支持 IPC URL |
| `scripts/container_run_ipc.sh` | 新增 | IPC 模式启动脚本 |
| `scripts/test_zmq_ipc_client.py` | 新增 | IPC 客户端测试 |

## 结论

1. **IPC 方案在容器内完全可行**，服务器正常工作
2. **macOS 主机无法访问 Linux IPC socket**，这是平台限制
3. **推荐使用方案 1（容器内运行）或方案 3（HTTP 桥接）** 作为主机访问方式
