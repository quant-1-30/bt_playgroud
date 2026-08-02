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
xtp_api_python/
├── pyproject.toml              # Poetry：xtp-service 包（仅 server）
├── config.example.toml         # 配置模板（拷为 config.toml）
├── xtp_service/                # 镜像内唯一 Python 包
│   ├── config.py               #   配置加载（env + config.toml）
│   ├── api/                    #   XTP 原生封装（容器内 Linux/ARM only）
│   │   ├── trader_service.py   #     TraderApi：SPI C++线程→asyncio.Queue 桥接
│   │   └── quote_service.py    #     QuoteApi：行情推送→asyncio
│   ├── protocol/codec.py       #   msgpack 编解码
│   ├── rpc/                    #   method→handler 分发
│   │   ├── registry.py
│   │   └── handlers.py
│   └── transport/zmq_server.py #   ROUTER+DEALER+worker，去掉 bt_sdk 依赖
├── client/                     # bt_studio 拷贝这两个文件即用
│   ├── zmq_client.py           #   纯 asyncio DEALER：call()/subscribe()
│   └── codec.py                #   msgpack 编解码（与 server 一致）
├── scripts/run_server.py       # 容器入口：加载库→登录→注册handler→起ZMQ
├── tests/                      # 协议 + ZMQ loopback（macOS 可跑，无需原生库）
├── source/Linux/...            # XTP C++ 源码 + 编译产物（vnxtp*.so / libxtp*api.so）
└── XTP_API_20250806_2.2.50.8/  # 官方 SDK（头文件 + 各平台 .so/.dll）
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

### 3. 启动

```bash
poetry run python scripts/run_server.py
# 或
poetry run xtp-server
```

启动后日志会打印 `ZMQ server listening on tcp://0.0.0.0:5570`。容器只需对外暴露 `5570` 端口。

### 4. 原生库加载说明

`xtp_service/api/__init__.py` 的 `load_native_libs()` 会把 `source/Linux/xtp_api_python3_2.2.50.8/` 与其 `xtpapi/` 子目录加入 `sys.path` 与 `LD_LIBRARY_PATH`，使 `import vnxtptrader` / `import vnxtpquote` 可用。若在 macOS 直接运行 `run_server.py`，会在登录阶段抛 `无法加载 vnxtp*.so`，属预期行为——协议与 ZMQ 层可独立测试。

## 二、宿主机侧（bt_studio）接入

### 1. 拷贝两个文件

把本仓库的 `client/zmq_client.py` 与 `client/codec.py` 拷贝到 bt_studio 项目中（任意目录，二者需在同一目录）。

### 2. 安装依赖（在 bt_studio 的 Poetry 项目里）

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
| `trader.subscribe_events` | 流式 | 订阅订单/成交主动推送 |
| `quote.subscribe_market_data` | 单响应 | 订阅行情，params `{tickers: [{ticker, exchange_id}]}` |
| `quote.subscribe_events` | 流式 | 订阅行情推送 |

> 流式 = 多帧响应 + 末尾 `eof`；client 的 `call()` 返回最后一帧，`subscribe()` 逐帧 yield。

## 四、协议帧格式

- 上行（client → server）：`[req_id, payload]`
- 下行（server → client）：`[identity, req_id, payload]` × N + `[identity, req_id, b'eof']`
- `payload` 为 msgpack：请求 `{method, params}` / 响应 `{result}` 或 `{error: {code, message}}`

## 五、测试

协议与 ZMQ loopback 测试不依赖 XTP 原生库，可在 macOS 直接运行：

```bash
poetry install
poetry run pytest tests/ -v
```

- `tests/test_protocol.py`：msgpack 编解码往返、中文字段、client/server codec 互通。
- `tests/test_zmq_loopback.py`：真实启动 ZMQ server + XtpClient，验证 ping/pong 与流式多帧。

## 六、旧版说明

- 原 `plugins/zmq_svr.py` / `plugins/zmq_client.pyx`（依赖外部 `bt_sdk`/`bt_quote`/`core.rpc.client`）已重构为本仓库的 `xtp_service/transport/zmq_server.py` 与 `client/zmq_client.py`，并去掉了外部包依赖。
- `test/tradertest.py` / `test/quote_login_test.py` 等仍为容器内集成测试，运行时需 `LD_LIBRARY_PATH` 指向 `.so` 所在目录。
- XTP 官方接口语义见 `source/.../xtpapi/xtp_trader_api.h`、`xtp_quote_api.h`；Python 封装方法名首字母小写，其余与 C++ 一致。

"""ZMQ Server：ROUTER + DEALER + worker pool

frmae：
- client → frontend ``[req_id, payload]``
- frontend → backend ``[identity, req_id, payload]``
- worker backend → frontend → client ``[identity, req_id, payload]`` * N + ``[identity, req_id, b'eof']``
"""

"""XTP TraderApi 封装：把 C++ SPI 回调桥接到 asyncio。

设计要点：
- XTP 的 SPI 回调发生在 C++ 线程，必须用 `loop.call_soon_threadsafe` 跨线程投递
- 每个查询类请求（queryAsset/queryPosition 等）可能返回多帧（is_last=False → True），
  用 `asyncio.Queue` 按 reqid 挂起，handler `async for` 取出；
- 订单事件（onOrderEvent/onTradeEvent）是主动推送，用单独的广播队列给所有订阅者。
- SPI 类必须在容器内 `import vnxtptrader` 成功后才能继承 TraderApi，
  因此用工厂函数 :func:`_make_spi_class` 延迟创建。
"""

"""容器内启动入口：加载原生库 → 登录 XTP → 注册 handlers → 启动 ZMQ serve
运行方式容器内
    poetry run python scripts/run_server.py
    # 或
    poetry run xtp-server
"""
