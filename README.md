# XTP + ZMQ RPC 量化交易服务

中泰证券 XTP 极速交易/行情 Python API 的工程化封装。本仓库已重构为标准 Poetry 工程，通过 **ZMQ** 把 XTP 能力以 **RPC** 形式对外暴露，适配 **「XTP 服务跑在 ARM 容器、bt_studio 跑在宿主机」** 的部署拓扑。

## 架构总览

```
┌───────────────────────────────────┐         ┌───────────────────────────────────┐
│  XTP 服务（ARM Linux 容器）        │  ZMQ    │  bt_studio（宿主机 macOS）          │
│  本仓库 Poetry 包 xtp_service      │  TCP    │  独立 Poetry 项目                  │
│  • vnxtp*.so + libxtp*api.so      │◀───────▶│  • 拷入 client/zmq_client.py       │
│  • ZMQ Server（仅暴露端口）        │         │  • 拷入 client/codec.py            │
│  • TraderApi / QuoteApi 封装       │         │  • import zmq_client 调用           │
└───────────────────────────────────┘         └───────────────────────────────────┘
```

- **本仓库（容器内）= 只含 ZMQ Server**：加载 XTP 原生库，登录交易/行情，通过 ROUTER+DEALER+worker pool 对外提供 RPC。
- **bt_studio（宿主机）**：拷贝 `client/zmq_client.py` + `client/codec.py` 两个文件即可，依赖 `pyzmq` + `msgpack`，无需 XTP 原生库。

## 目录结构

```
bt_playgroud/
├── pyproject.toml              # Poetry：xtp-service 包（仅 server）
├── xtp_service/                # 镜像内唯一 Python 包
│   ├── config.py               #   配置加载（env + config.toml）
│   ├── api/                    #   XTP 原生封装（容器内 Linux/ARM only）
│   │   ├── trader_service.py   #     TraderApi：SPI C++线程→asyncio.Queue 桥接
│   │   └── quote_service.py    #     QuoteApi：行情推送→asyncio
│   ├── protocol/codec.py       #   msgpack 编解码 + RpcErrorCode
│   ├── rpc/                    #   method→handler 分发
│   │   ├── registry.py         #     含 HandlerEntry / is_on_subscribe
│   │   └── handlers.py
│   └── transport/
│       ├── zmq_server.py       #   ROUTER+DEALER+zmq.proxy+worker pool
│       └── pubsub.py           #   BroadcastHub / Broadcaster（长连接订阅解耦）
├── client/                     # bt_studio 拷贝这两个文件即用
│   ├── zmq_client.py           #   纯 asyncio DEALER：call()/subscribe()
│   └── codec.py                #   msgpack 编解码（与 server 一致）
├── scripts/run_server.py       # 容器入口：加载库→登录→注册handler→起ZMQ
├── tests/                      # 协议 + ZMQ loopback + pubsub + error + timeout（macOS 可跑）
└── docs/PERFORMANCE_AUDIT.md   # 性能审计与修复记录
```

## 一、容器侧（XTP 服务）部署

### 1. 安装依赖

```bash
poetry install
```

> 运行环境必须是 ARM Linux 容器，且 `source/Linux/xtp_api_python3_2.2.50.8/` 下有编译好的 `vnxtp*.so` 与 `xtpapi/libxtp*api.so`。

### 2. 配置

```bash
cp config.example.toml config.toml
# 编辑 config.toml 填入真实交易/行情账号，或改用环境变量：
#   XTP_TRADER_USER / XTP_TRADER_PASSWORD / XTP_TRADER_KEY ...
#   XTP_QUOTE_USER  / XTP_QUOTE_PASSWORD ...
#   ZMQ_HOST / ZMQ_PORT / ZMQ_BACKEND_PORT ...
```

#### ZMQ 配置项（`[zmq]` section）

| 字段 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `host` | `ZMQ_HOST` | `0.0.0.0` | 监听地址 |
| `port` | `ZMQ_PORT` | `5570` | frontend 端口 |
| `backend_port` | `ZMQ_BACKEND_PORT` | `5571` | backend 端口 |
| `max_workers` | `ZMQ_WORKERS` | `8` | worker 数量 |
| `hwm` | `ZMQ_HWM` | `4096` | 高水位标记（原 1000 已提升） |
| `pubsub_maxsize` | `ZMQ_PUBSUB_MAXSIZE` | `4096` | 每个订阅者队列容量 |
| `query_timeout` | `ZMQ_QUERY_TIMEOUT` | `15.0` | XTP 查询单帧超时（秒） |
| `enable_ipv6` | `ZMQ_ENABLE_IPV6` | `false` | 是否启用 IPv6 |

### 3. 启动

```bash
poetry run python scripts/run_server.py
# 或
poetry run xtp-server
```

## 二、宿主机侧（bt_studio）接入

### 1. 拷贝两个文件

把本仓库的 `client/zmq_client.py` 与 `client/codec.py` 拷贝到 bt_studio 项目中。

### 2. 安装依赖

```bash
cd /path/to/bt_studio
poetry add pyzmq msgpack
```

### 3. 调用示例

```python
import asyncio
from zmq_client import XtpClient

async def main():
    client = XtpClient("tcp://<容器IP>:5570", timeout=10.0)
    await client.connect()

    # 健康检查
    print(await client.ping())

    # 单响应：查询资产
    asset = await client.call("trader.query_asset", {})
    print(asset)

    # 流式响应：订阅订单事件
    async for frame in client.subscribe("trader.subscribe_events", {}):
        print("order event:", frame)

    await client.close()

asyncio.run(main())
```

> **IPv6 说明**：`XtpClient` 默认关闭 IPv6（`enable_ipv6=False`）。如果容器网络需要 IPv6，传 `XtpClient(endpoint, enable_ipv6=True)`。

## 三、RPC 方法清单

| method | 类型 | 说明 |
|---|---|---|
| `ping` | 单响应 | 健康检查，返回 `{pong, trader_started, quote_started}` |
| `trader.query_asset` | 流式 | 查询资金账户 |
| `trader.query_position` | 流式 | 查询持仓，params `{ticker}` |
| `trader.query_order` | 流式 | 查询报单，params `{req}` |
| `trader.query_trade` | 流式 | 查询成交，params `{req}` |
| `trader.query_account_trade_market` | 流式 | 查询可交易市场 |
| `trader.insert_order` | 单响应 | 下单，params `{req}`，返回 `{order_xtp_id}` |
| `trader.cancel_order` | 单响应 | 撤单，params `{order_xtp_id}`，返回 `{ret}` |
| `trader.subscribe_events` | 流式(订阅) | 订阅订单/成交主动推送 |
| `quote.subscribe_market_data` | 单响应 | 订阅行情，params `{tickers: [{ticker, exchange_id}]}`，按 exchange_id 分组 |
| `quote.subscribe_events` | 流式(订阅) | 订阅行情推送 |

> **错误帧语义**：错误始终通过 `error` 字段返回（`{error: {code, message}}`），不会被包装为 `result`。客户端通过检查 `error` 键判断成功/失败。

### 错误码

| 码 | 常量 | 含义 |
|---|---|---|
| -32700 | PARSE_ERROR | 请求解析失败 |
| -32601 | METHOD_NOT_FOUND | 方法未注册 |
| -32602 | INVALID_PARAMS | 参数缺失或类型错误 |
| -32000 | INTERNAL_ERROR | 内部异常 |
| -32001 | SERVICE_STOPPED | 服务正在关闭 |
| -32004 | RATE_LIMITED | 触发限流 |
| -32010 | QUERY_TIMEOUT | XTP 查询超时（SPI 未回调） |
| -32011 | SUBSCRIBER_GONE | 订阅者已断开 |

## 四、协议帧格式

- 上行（client → server）：`[req_id, payload]`
- 下行（server → client）：`[identity, req_id, payload]` × N + `[identity, req_id, b'eof']`
- `payload` 为 msgpack：请求 `{method, params}` / 响应 `{result}` 或 `{error: {code, message}}`

> **重要变更**：dispatch 产出的 `{"error": {...}}` 帧现在编码为 `{error: {code, message}}`（通过 `encode_response(error=...)`），不再被错误包装为 `{result: {error: ...}}`。

## 五、测试

协议与 ZMQ loopback 测试不依赖 XTP 原生库，可在 macOS 直接运行：

```bash
poetry install
python3 -m pytest tests/ -v
```

测试覆盖：
- `test_protocol.py`：msgpack 编解码往返、中文字段、client/server codec 互通、encode_error、unpack_request 容错。
- `test_zmq_loopback.py`：真实启动 ZMQ server + XtpClient，验证 ping/pong 与流式多帧。
- `test_zmq_loopback_ext.py`：订阅不阻塞 worker pool、突发 5000 帧背压。
- `test_error_frames.py`：错误帧走 `error` 字段、方法未找到返回 -32601、handler 异常返回 -32000。
- `test_pubsub.py`：BroadcastHub subscribe/publish/unsubscribe/丢弃策略/Broadcaster 帧格式。
- `test_query_timeout.py`：XTP 查询超时保护、SPI 不回调时不泄漏协程。