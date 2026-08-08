# 架构与模块集成文档

> 本文档描述 `bt_playgroud` 的模块架构、依赖关系、数据流，以及各模块在主服务中的集成状态。

## 一、系统架构总览

```
┌──────────────────────────── 容器侧（ARM Linux）────────────────────────────┐
│                                                                            │
│  scripts/run_server.py（入口）                                              │
│    │                                                                       │
│    ├── load_native_libs() → 加载 vnxtptrader.so / vnxtpquote.so            │
│    ├── TraderService.start() → 登录 XTP 交易                               │
│    ├── QuoteService.start() → 登录 XTP 行情                                │
│    ├── register_handlers(trader, quote) → 注册 RPC 方法                    │
│    ├── ZmqServer.setup_hub("trader.subscribe_events")                      │
│    ├── ZmqServer.setup_hub("quote.subscribe_events")                       │
│    ├── Broadcaster(trader_source_q, trader_hub, server.frontend)           │
│    ├── Broadcaster(quote_source_q, quote_hub, server.frontend)             │
│    └── ZmqServer.start() → 启动 zmq.proxy + workers                        │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                        ZmqServer                                     │  │
│  │                                                                      │  │
│  │  ┌─────────┐    zmq.proxy     ┌─────────┐    DEALER     ┌─────────┐  │  │
│  │  │ ROUTER  │◀───────────────▶│ DEALER  │◀─────────────▶│ Workers │  │  │
│  │  │(sync)   │   (C-level)      │(sync)   │   (asyncio)   │ (N个)   │  │  │
│  │  └─────────┘                  └─────────┘               └────┬────┘  │  │
│  │       ▲                                                     │       │  │
│  │       │              [identity, req_id, payload]            │       │  │
│  │       │           ◀─────────────────────────────────────────┘       │  │
│  │       │                                                             │  │
│  │       │  Broadcaster 推送帧                                         │  │
│  │       │  [identity, req_id, body]                                   │  │
│  │       │                                                             │  │
│  │  ┌────┴────────────────────────────────────────────────────────┐   │  │
│  │  │  Worker._worker()                                            │   │  │
│  │  │    1. unpack_request(payload)                                │   │  │
│  │  │    2. if is_on_subscribe(method) and method in hubs:         │   │  │
│  │  │         → _handle_subscription (非阻塞: hub.subscribe + ack) │   │  │
│  │  │    3. else:                                                  │   │  │
│  │  │         → _handle_request → registry.dispatch                │   │  │
│  │  └─────────────────────────────────────────────────────────────┘   │  │
│  │                                                                      │  │
│  │  self.hubs: dict[str, BroadcastHub]                                  │  │
│  │    "trader.subscribe_events" → BroadcastHub                          │  │
│  │    "quote.subscribe_events"  → BroadcastHub                          │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                        Broadcaster (trader)                           │  │
│  │  source_queue ← trader.subscribe_events() 返回的 Queue                │  │
│  │       │                                                               │  │
│  │       ▼                                                               │  │
│  │  hub.publish(frame) → fan-out 到所有 Subscriber.queue                 │  │
│  │       │                                                               │  │
│  │       ▼                                                               │  │
│  │  server.frontend.send_multipart([identity, req_id, body])             │  │
│  │       │                                                               │  │
│  │       ▼  (通过 zmq.proxy ROUTER 推回客户端)                            │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
         │ TCP 5570
         ▼
┌──────────────────────────── 宿主机侧（macOS）──────────────────────────────┐
│  client/zmq_client.py                                                      │
│    XtpClient(endpoint)                                                     │
│      .connect() → DEALER socket                                            │
│      .call(method, params) → 单响应                                        │
│      .subscribe(method, params) → 流式 yield                               │
│      .ping()                                                               │
└────────────────────────────────────────────────────────────────────────────┘
```

## 二、模块清单与集成状态

### 协议层 `xtp_service/protocol/`

| 文件 | 关键符号 | 集成位置 | 状态 |
|---|---|---|---|
| `codec.py` | `RpcErrorCode` | zmq_server, handlers, registry, trader_service | ✅ 已集成 |
| `codec.py` | `RpcError` | handlers (raise), registry (catch), zmq_server (catch) | ✅ 已集成 |
| `codec.py` | `encode_error()` | zmq_server (_handle_request, _handle_subscription) | ✅ 已集成 |
| `codec.py` | `unpack_request()` | zmq_server (_worker) | ✅ 已集成（含容错） |
| `codec.py` | `encode_response()` | zmq_server, pubsub.Broadcaster | ✅ 已集成 |
| `__init__.py` | 导出 EOF/Request/Response | — | ⚠️ 未导出 RpcErrorCode/RpcError/encode_error（但不影响，各模块直接 import codec） |

### 配置层 `xtp_service/config.py`

| 字段 | 读取位置 | 状态 |
|---|---|---|
| `hwm` | zmq_server (_start_proxy, _worker), client (set_hwm) | ✅ |
| `pubsub_maxsize` | zmq_server (setup_hub → BroadcastHub maxsize) | ✅ |
| `query_timeout` | run_server → TraderService(query_timeout=...) | ✅ |
| `enable_ipv6` | client (XtpClient enable_ipv6 param) | ✅ |

### 接口层 `xtp_service/api/`

| 文件 | 关键方法 | 集成位置 | 状态 |
|---|---|---|---|
| `trader_service.py` | `_do_query(timeout)` | handlers 调用 query_asset/query_position 等 | ✅ |
| `trader_service.py` | `_force_wake_all()` | stop() 调用 | ✅ |
| `trader_service.py` | `subscribe_events()` | run_server 创建 Broadcaster source queue | ✅ |
| `trader_service.py` | `SubscriberHandle` | — | 🔶 **已定义但未被使用**（subscribe_events 返回裸 Queue，未包装为 Handle） |
| `quote_service.py` | `subscribe_market_data(分组)` | handlers._sub_md 调用 | ✅ |
| `quote_service.py` | `_force_wake_all()` | stop() 调用 | ✅ |
| `quote_service.py` | `subscribe_events()` | run_server 创建 Broadcaster source queue | ✅ |

### RPC 层 `xtp_service/rpc/`

| 文件 | 关键符号 | 集成位置 | 状态 |
|---|---|---|---|
| `registry.py` | `register(subscription=True)` | handlers.py 标记 subscribe_events | ✅ |
| `registry.py` | `is_on_subscribe()` | zmq_server._worker 判断是否走快路径 | ✅ |
| `registry.py` | `dispatch()` | zmq_server._handle_request | ✅ |
| `registry.py` | `HandlerEntry` | registry 内部 _handlers dict | ✅ 内部使用 |
| `registry.py` | `dispatch_stream()` | — | 🔶 **已定义但未被调用**（与 dispatch 实现相同，预留扩展） |
| `handlers.py` | `register_handlers()` | run_server 调用 | ✅ |
| `handlers.py` | 参数校验 (insert_order/cancel_order) | dispatch → handler 内 raise RpcError | ✅ |

### 传输层 `xtp_service/transport/`

| 文件 | 关键符号 | 集成位置 | 状态 |
|---|---|---|---|
| `zmq_server.py` | `zmq.proxy` | _start_proxy → run_in_executor | ✅ |
| `zmq_server.py` | `_handle_subscription()` | _worker 调用（is_on_subscribe=True 且 hub 存在时） | ✅ |
| `zmq_server.py` | `setup_hub()` | run_server 调用 | ✅ |
| `zmq_server.py` | `_handle_request(error frame fix)` | _worker 调用（非订阅请求） | ✅ |
| `pubsub.py` | `BroadcastHub` | zmq_server.hubs 持有 | ✅ |
| `pubsub.py` | `Broadcaster` | run_server 创建 + start/stop | ✅ |
| `pubsub.py` | `Broadcaster.run()` | 从 source_queue 取帧 → publish → socket send | ✅ |
| `pubsub.py` | `publish_nowait()` | — | 🔶 **已定义但未被调用**（C++ 线程安全版本，当前 SPI 回调经 call_soon_threadsafe 走 async publish） |

### 客户端 `client/`

| 文件 | 关键特性 | 状态 |
|---|---|---|
| `zmq_client.py` | `enable_ipv6` 参数 | ✅ |
| `zmq_client.py` | `close()` 幂等 | ✅ |
| `zmq_client.py` | `subscribe` 超时日志 | ✅ |
| `codec.py` | 与 server protocol/codec.py 一致 | ✅ |

## 三、数据流详解

### 普通请求（如 ping, query_asset）

```
Client.call("ping", {})
  → DEALER.send([req_id, msgpack({method:"ping", params:{}})])
  → zmq.proxy ROUTER → DEALER → Worker
  → Worker._worker: unpack_request → is_on_subscribe? No
  → Worker._handle_request → registry.dispatch(request)
  → handler 返回 {pong: True}
  → encode_response(result={pong:True})
  → Worker.send([identity, req_id, body])
  → Worker.send([identity, req_id, EOF])
  → zmq.proxy DEALER → ROUTER → Client
  → Client._recv_loop → decode_payload → q.put
  → Client.call 返回 last frame
```

### 订阅请求（如 trader.subscribe_events）

```
Client.subscribe("trader.subscribe_events", {})
  → DEALER.send([req_id, msgpack({method, params})])
  → zmq.proxy → Worker
  → Worker._worker: unpack_request → is_on_subscribe? Yes, hub exists? Yes
  → Worker._handle_subscription:
      hub.subscribe(identity, req_id) → 创建 Subscriber
      send([identity, req_id, encode_response({subscribed:True})])  ← ack
      send([identity, req_id, EOF])
      return  ← Worker 立即释放！
  → Client 收到 ack {subscribed:True}
```

### 事件推送（Broadcaster 驱动）

```
C++ SPI 回调 (onOrderEvent/onTradeEvent)
  → TraderService._on_event (C++ 线程)
  → loop.call_soon_threadsafe(q.put_nowait, frame)
  → trader._event_subscribers[].queue ← frame

Broadcaster.run() (async task):
  → frame = await source_queue.get()
  → hub.publish(frame) → fan-out 到所有 Subscriber.queue
  → for each Subscriber:
      server.frontend.send([identity, req_id, encode_response(result=frame)])
  → zmq.proxy ROUTER → Client
  → Client._recv_loop → yield frame
```

### 查询超时保护

```
Client.call("trader.query_asset", {})
  → Worker._handle_request → dispatch → handler → trader.query_asset()
  → TraderService._do_query:
      reqid = next_reqid()
      q = register_query(reqid)
      api.queryAsset(session_id, reqid)  → C++ 发出查询
      ┌─ if SPI 回调 in query_timeout 秒:
      │    frame = await q.get()  ← SPI 回调经 _on_query put 到 queue
      │    yield frame
      │    if frame.last: return
      └─ else (超时):
           yield {error: {code: QUERY_TIMEOUT(-32010), message: "..."}}
           return  ← 协程退出，worker 释放
```

## 四、已定义但未集成的 API（预留）

以下 API 已定义并有单元测试，但在生产数据流中暂未被调用。它们是为未来扩展预留的接口，不影响当前功能。

| 符号 | 定义位置 | 设计意图 | 当前替代 |
|---|---|---|---|
| `SubscriberHandle` | `trader_service.py` | 封装 queue + unsubscribe 回调供 pubsub 层使用 | `subscribe_events()` 返回裸 `asyncio.Queue`，Broadcaster 直接用 |
| `dispatch_stream()` | `registry.py` | 显式标记流式分发的入口 | `dispatch()` 已统一处理 async generator |
| `publish_nowait()` | `pubsub.py` | C++ 线程安全的同步 fan-out | SPI 回调经 `call_soon_threadsafe` 走 async `publish()` |

## 五、关闭顺序

```
SIGINT/SIGTERM
  → _shutdown():
      1. server.stop()
         → shutdown_event.set()
         → close proxy sockets → zmq.proxy 退出
         → cancel workers → gather
         → close frontend/backend → context.term
      2. broadcaster.stop() (trader + quote 各一个)
         → source_queue.put(None)
         → cancel task
         → hub.unsubscribe_all()
      3. trader.stop()
         → api.logout + exit
         → _force_wake_all (put None 到所有 query/event queue)
      4. quote.stop()
         → api.exit
         → _force_wake_all
```

## 六、测试覆盖与模块映射

| 测试文件 | 覆盖模块 | 覆盖点 |
|---|---|---|
| `test_protocol.py` | protocol/codec.py | RpcErrorCode, encode_error, unpack_request 容错 |
| `test_query_timeout.py` | api/trader_service.py | _do_query 超时保护 |
| `test_pubsub.py` | transport/pubsub.py | BroadcastHub + Broadcaster |
| `test_error_frames.py` | transport/zmq_server.py | 错误帧走 error 字段 |
| `test_zmq_loopback.py` | transport/zmq_server.py + client/ | ping/pong + 流式回归 |
| `test_zmq_loopback_ext.py` | transport/zmq_server.py | 订阅不阻塞 worker + 突发背压 |