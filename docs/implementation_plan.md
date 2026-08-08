# Implementation Plan

[Overview]
本计划针对 `bt_playgroud` 的第三方接口（XTP Trader/Quote API 封装）与交易通信组件（ZMQ ROUTER/DEALER RPC + msgpack 协议）做一次「性能卡点 + 逻辑 bug」的全面修复，目标是消除在真实交易/行情订阅场景下会触发卡死、丢帧、内存泄漏与错误帧误传的缺陷，同时把通信层吞吐与延迟优化到接近 ZMQ 原生能力。

项目当前架构为：容器侧 `xtp_service` 加载 XTP 原生 `.so`，通过 ROUTER(frontend) + DEALER(backend) + N worker（每个 worker 一个 DEALER connect backend）的拓扑对外暴露 RPC；宿主机侧 `client/zmq_client.py` 以 DEALER 调用。调查发现当前实现存在 **12 处** 明确的性能或正确性问题，其中 **3 处**（订阅阻塞 worker pool、XTP 查询无超时、错误帧被包装成 result）会直接导致交易/行情不可用或客户端误判成功，属于必须修复的关键缺陷。

修复将遵循四条原则：(1) wire 协议向后兼容，bt_studio 端 `client/` 无需强制升级；(2) 长连接订阅必须从 worker pool 解耦；(3) 所有阻塞点必须有超时与背压保护；(4) 错误必须以 `error` 字段而非 `result` 字段返回。修改集中在 `xtp_service/transport/zmq_server.py`、`xtp_service/rpc/registry.py`、`xtp_service/rpc/handlers.py`、`xtp_service/api/trader_service.py`、`xtp_service/api/quote_service.py`、`client/zmq_client.py`，并新增 `xtp_service/transport/pubsub.py` 用于解耦广播。

[Types]

本计划不引入新的对外公开类型，但会在内部新增/补全若干 dataclass 与常量，统一错误码与帧类型标识。

```python
# xtp_service/protocol/codec.py —— 新增显式错误码常量与帧类型
EOF = b"eof"

class RpcErrorCode:
    PARSE_ERROR      = -32700
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS   = -32602
    INTERNAL_ERROR   = -32000
    SERVICE_STOPPED  = -32001
    RATE_LIMITED     = -32004
    QUERY_TIMEOUT    = -32010
    SUBSCRIBER_GONE  = -32011

# xtp_service/transport/pubsub.py —— 新增广播器
@dataclass
class Subscriber:
    identity: bytes          # client 的 ROUTER identity
    req_id: bytes            # 订阅请求的 req_id（用于帧路由）
    queue: asyncio.Queue     # 该订阅者的有界队列（maxsize=HWM）
    alive: bool = True

class BroadcastHub:
    """单进程内的发布/订阅广播器。
    每个 trader/quote 事件源对应一个 hub；每个订阅客户端对应一个 Subscriber。
    事件源把帧 put 到 hub，hub fan-out 到所有 Subscriber.queue。
    队列满时丢弃最旧帧并计数（行情可丢、订单事件不丢，由 policy 决定）。"""
    def __init__(self, maxsize: int = 4096, drop_policy: str = "drop_oldest"): ...
    def subscribe(self, identity, req_id) -> Subscriber: ...
    def unsubscribe(self, sub: Subscriber) -> None: ...
    async def publish(self, frame: dict) -> None: ...   # fan-out
    def publish_nowait(self, frame: dict) -> int: ...   # C++ 线程用，返回丢弃数
```

```python
# xtp_service/api/trader_service.py —— 查询超时配置
@dataclass
class QueryOptions:
    timeout: float = 15.0          # 单帧等待超时（秒），覆盖 _do_query 的 await q.get()
    idle_timeout: float = 30.0     # 两帧间最大间隔
```

`ZmqServerConfig` 新增字段（向后兼容，全部有默认值）：

```python
@dataclass
class ZmqServerConfig:
    host: str = "0.0.0.0"
    port: int = 5570
    backend_port: int = 5571
    max_workers: int = 8
    rate_limit_per_minute: int = 0
    ipc_path: str = ""
    hwm: int = 4096                 # 新增：frontend/backend/worker 共用 HWM（原 1000 过低）
    pubsub_maxsize: int = 4096      # 新增：每个订阅者队列容量
    query_timeout: float = 15.0     # 新增：XTP 查询单帧超时（透传给 TraderService）
    enable_ipv6: bool = False       # 新增：明确控制 IPv6（替代 client 的魔术值 42）
```

[Files]

本次修改覆盖通信层、接口层、协议层与客户端；wire 协议向后兼容，`client/` 仅做可选增强。

**新增文件：**

- `xtp_service/transport/pubsub.py`（新建）：`BroadcastHub` / `Subscriber`，把长连接订阅从 worker pool 解耦。hub 由独立 asyncio task 驱动 fan-out，worker 只负责接受订阅请求并立即返回 ack，不再阻塞。
- `tests/test_pubsub.py`（新建）：单测 `BroadcastHub` 的 subscribe/publish/ unsubscribe/丢弃策略/满队列行为。
- `tests/test_error_frames.py`（新建）：验证错误帧以 `error` 字段返回、错误码正确、客户端能识别。
- `tests/test_query_timeout.py`（新建）：模拟 XTP C++ 不回调时 `_do_query` 在 `query_timeout` 后超时返回错误帧。

**修改文件：**

- `xtp_service/transport/zmq_server.py`
  - **删除** `_proxy_task` 的 Python poller 实现，替换为 C 级 `zmq.proxy(frontend, backend)` 在独立线程/任务中运行（`zmq.device` / `asyncio.to_thread`）。这是最大的吞吐优化。
  - 把 `frontend`/`backend` 的 `set_hwm(1000)` 改为 `set_hwm(cfg.hwm)`，默认 4096。
  - 修正 `_handle_request` 的错误帧编码：当 dispatch 产出的 `frame` 是 dict 且含 `error` 键时，必须调用 `encode_response(error=frame["error"])`，**不能**走 `encode_response(result=frame)` 分支（当前 `zmq_server.py:177` 的 bug）。
  - 流式帧批量化：当 dispatch 产出连续多帧时，用 `SNDMORE` 累积后一次性 `send_multipart`（注意 ROUTER 帧格式要求每次 `[identity, req_id, body]` 完整，故批量化收益主要在减少 `await` 次数，通过 `asyncio.Queue` 预取实现）。
  - 新增 pubsub worker 路由：识别 `trader.subscribe_events` / `quote.subscribe_events` 方法的请求，**不**进入普通 worker 的 `async for dispatch`，而是注册到对应 `BroadcastHub`，由广播 task 负责持续推送；worker 收到订阅请求后立即返回一个 ack 帧并结束（不占 worker）。
  - `stop()` 增加优雅排空：先停止接受新请求，给 in-flight 请求发 `SERVICE_STOPPED` 错误帧 + EOF，再关闭 socket。
  - worker 在 `ETERM` 后 `worker_socket.close()` 包 `try/except`。

- `xtp_service/rpc/registry.py`
  - `dispatch` 的异常分支（`registry.py:53-54`）当前 yield `{"error": {...}}`，这是正确的；但需要补一个 **方法未注册** 的错误码（已有 -32601）和 **参数校验失败** 的 -32602。
  - 新增 `dispatch_stream(request)` 与 `is_on_subscribe(method)` 辅助方法，让 server 能区分「一次性/普通流式」与「长连接订阅」，从而决定是否走 pubsub 通道。
  - 增加方法注册的元信息：`register(method, *, streaming=False, subscription=False)`，handlers 用它标记 `subscribe_events`。

- `xtp_service/rpc/handlers.py`
  - `trader.subscribe_events` / `quote.subscribe_events` 改为：注册 `subscription=True`，handler 本身只做 `hub.subscribe(...)` 并返回 ack；真正的帧推送由 `pubsub.Broadcaster` 从 `TraderService._event_subscribers` / `QuoteService._subs` 取帧后 fan-out。
  - 修正 `trader.cancel_order`（`handlers.py:50`）：`params["order_xtp_id"]` → 校验存在且为整数，缺失时返回 `INVALID_PARAMS` 错误帧而非抛 KeyError。
  - `trader.insert_order` 同样校验 `params.get("req")` 为 dict。
  - 错误统一通过 `raise RpcError(code, message)` 或返回 `{"error": {...}}`，不再依赖 registry 的 try/except 兜底（兜底仍在，但作为最后防线）。

- `xtp_service/api/trader_service.py`
  - `_do_query`（`trader_service.py:230-247`）：`await q.get()` → `await asyncio.wait_for(q.get(), timeout=cfg.query_timeout)`，超时 yield `{"error": {"code": QUERY_TIMEOUT, "message": "XTP query timeout"}}` 并 `break`。
  - 新增两帧间 idle_timeout（覆盖 SPI 只回了部分帧就不再回的情况）。
  - `stop()` 中的 `_safe_put`（`trader_service.py:174-177, 300-304`）：改为向每个 query queue **强制 put(None)**（用 `put_nowait` 失败时直接 `task.cancel()` 消费协程），保证 `_do_query` 的 `await q.get()` 一定被唤醒，避免协程泄漏。event subscribers 同理 put None 后从列表移除。
  - `subscribe_events` 返回的对象除 queue 外，额外封装为 `SubscriberHandle`（含 unsubscribe 回调），供 pubsub 层使用；保留旧签名以兼容。
  - `_to_serializable`（`trader_service.py:12-26`）：保留但加注释说明它跑在 C++ 线程；msgpack 编码时改用 `msgpack.packb(obj, default=_bytes_to_str, use_bin_type=True)` 把两步合并为一步，减少 per-frame 开销。

- `xtp_service/api/quote_service.py`
  - `subscribe_market_data`（`quote_service.py:84-86`）：移除 `eid = tl[0].get("exchange_id", 2)` 的「只用第一个 exchange」假设；改为按 `exchange_id` 分组分别调用 `subscribeMarketData`，聚合返回值。
  - `unsubscribe_market_data` 同理。
  - `stop()` 中的 `try: q.put_nowait(None); except: pass`（`quote_service.py:71-73`）改为强制唤醒（同 trader）。

- `xtp_service/protocol/codec.py`
  - 新增 `RpcErrorCode` 常量类。
  - 新增 `encode_error(code: int, message: str) -> bytes` 便捷函数，确保错误帧始终走 `error` 字段。
  - `unpack_request` 容错：`method` 缺失时抛 `RpcError(PARSE_ERROR)` 而非 `KeyError`。

- `xtp_service/config.py`
  - `ZmqServerConfig` 增加 `hwm`、`pubsub_maxsize`、`query_timeout`、`enable_ipv6` 字段（全部带默认值，向后兼容）；`load_settings` 同步读取 env / toml。

- `client/zmq_client.py`
  - `setsockopt(42, 1)`（`zmq_client.py:28`）→ `setsockopt(zmq.IPV6, 1 if enable_ipv6 else 0)`，并暴露 `enable_ipv6` 构造参数（默认 `False`，保持向后兼容；若用户原依赖 IPv6 显式传 `True`）。
  - `call()` 的 `finally`（`zmq_client.py:78-79`）：`self._queues.pop(rid, None)` 之外，若因超时退出且 server 后续仍回包，需要清空残留帧——当前已 pop 队列丢弃，OK；但需补一个 `_pending_drain` 防止 socket 缓冲区残留。
  - `subscribe()` 的 `except asyncio.TimeoutError: return`（`zmq_client.py:90`）：静默 return 会丢失 EOF 之前的帧语义，改为 yield 一个 timeout 指示或抛出受控异常，由调用方决定；默认行为保持 return 但记日志。
  - `close()`：`_recv_task` cancel 后，`self._ctx.term()` 可能阻塞，已有 `linger=0`，补 `self._socket.close(linger=0)` 幂等。

- `scripts/run_server.py`
  - 启动时实例化 `pubsub.Broadcaster` 并注入 handlers；优雅关闭顺序：`server.stop()` → `broadcaster.stop()` → `trader.stop()` → `quote.stop()`。

- `tests/test_zmq_loopback.py`
  - 现有 `test_ping_pong` / `test_streaming_frames` 保持通过；新增 `test_error_frame_not_wrapped_as_result`、`test_subscription_does_not_block_worker`（订阅一个流后，仍能 ping 成功，证明 worker 未被占满）。

[Functions]

本节按新增/修改/删除列出函数级变更，路径精确到文件。

**新增函数：**

- `xtp_service/transport/pubsub.py::BroadcastHub.__init__(maxsize, drop_policy)` — 初始化容量与丢弃策略。
- `xtp_service/transport/pubsub.py::BroadcastHub.subscribe(identity: bytes, req_id: bytes) -> Subscriber` — 创建有界队列并登记订阅者。
- `xtp_service/transport/pubsub.py::BroadcastHub.unsubscribe(sub: Subscriber) -> None` — 标记 `alive=False` 并移除。
- `xtp_service/transport/pubsub.py::BroadcastHub.publish(frame: dict) -> int` — fan-out 到所有订阅者，返回丢弃帧数；支持 `drop_oldest`（行情）与 `block`（订单）策略。
- `xtp_service/transport/pubsub.py::Broadcaster.__init__(source_queue: asyncio.Queue, hub: BroadcastHub, send_socket, log_name)` — 绑定一个事件源（trader 或 quote 的 push queue）到一个 hub + 推送 socket。
- `xtp_service/transport/pubsub.py::Broadcaster.run() -> None` — async task：从 `source_queue` 取帧 → `hub.publish` → 通过 `send_socket` 按 `[identity, req_id, body]` 发给每个订阅者；订阅者 socket 断开时自动 unsubscribe。
- `xtp_service/transport/pubsub.py::Broadcaster.stop() -> None` — 停止 task，清空订阅。
- `xtp_service/protocol/codec.py::encode_error(code: int, message: str) -> bytes` — 统一构造错误帧。
- `xtp_service/rpc/registry.py::RpcRegistry.is_on_subscribe(method: str) -> bool` — 判断方法是否标记为长连接订阅。
- `xtp_service/rpc/registry.py::RpcRegistry.dispatch_stream(request)` — 与 `dispatch` 相同但显式语义为「这是流式」，保留以便未来扩展。
- `xtp_service/api/trader_service.py::TraderService._force_wake_all() -> None` — stop 时强制唤醒所有等待中的 query/event 协程。

**修改函数：**

- `xtp_service/transport/zmq_server.py::ZmqServer.__init__` — 读取 `cfg.hwm` 等新字段；持有 `BroadcastHub` 字典（trader/quote 各一）。
- `xtp_service/transport/zmq_server.py::ZmqServer._create_sockets` — `set_hwm(cfg.hwm)`。
- `xtp_service/transport/zmq_server.py::ZmqServer.start` — 用 `asyncio.current_running_loop().run_in_executor(None, zmq.proxy, frontend, backend)` 或 `zmq.device` 替换 `_proxy_task`；注意 `zmq.proxy` 会阻塞直到 socket 关闭，需在 `stop()` 中 close 触发其返回。
- `xtp_service/transport/zmq_server.py::ZmqServer._proxy_task` — **删除**（被 `zmq.proxy` 取代）。
- `xtp_service/transport/zmq_server.py::ZmqServer._worker` — 在 `recv_multipart` 后判断：若 `rpc_registry.is_on_subscribe(method)` 则委托给 `_handle_subscription`，否则走原 `_handle_request`。
- `xtp_service/transport/zmq_server.py::ZmqServer._handle_request`（`zmq_server.py:144-189`）— 修复错误帧编码：检查 `frame` 是否含 `error` 键；流式帧批量预取。
- `xtp_service/transport/zmq_server.py::ZmqServer._handle_subscription`（新增逻辑，可复用方法名）— 注册到 hub，立即 ack，返回（不进入 `async for dispatch`）。
- `xtp_service/transport/zmq_server.py::ZmqServer.stop` — 增加优雅排空与 `Broadcaster.stop`。
- `xtp_service/rpc/registry.py::RpcRegistry.register`（`registry.py:20-25`）— 签名改为 `register(method, *, streaming=False, subscription=False)`，记录元信息。
- `xtp_service/rpc/registry.py::RpcRegistry.dispatch`（`registry.py:37-54`）— 保留 try/except 兜底，但确保错误 yield 为 `{"error": {...}}`；方法不存在用 `METHOD_NOT_FOUND`。
- `xtp_service/rpc/handlers.py::register_handlers` — 用新 `register(subscription=True)` 标记订阅方法；订阅 handler 改为「登记 + ack」；补 `cancel_order` / `insert_order` 参数校验。
- `xtp_service/api/trader_service.py::TraderService._do_query`（`trader_service.py:230-247`）— 加 `wait_for` 超时与 idle 超时。
- `xtp_service/api/trader_service.py::TraderService.stop`（`trader_service.py:165-177`）— 用 `_force_wake_all` 替换 `_safe_put` 循环。
- `xtp_service/api/trader_service.py::TraderService.subscribe_events`（`trader_service.py:289-293`）— 返回 `SubscriberHandle`（含 queue + unsubscribe）。
- `xtp_service/api/quote_service.py::QuoteService.subscribe_market_data` / `unsubscribe_market_data`（`quote_service.py:84-90`）— 按 exchange 分组。
- `xtp_service/api/quote_service.py::QuoteService.stop`（`quote_service.py:65-73`）— 强制唤醒。
- `xtp_service/protocol/codec.py::unpack_request`（`codec.py:32-34`）— 容错缺失字段。
- `xtp_service/config.py::ZmqServerConfig` / `load_settings` — 新字段。

**删除函数：**

- `xtp_service/transport/zmq_server.py::ZmqServer._proxy_task`（`zmq_server.py:95-117`）— 被 C 级 `zmq.proxy` 取代，避免每帧两次 Python/C 边界穿越。无迁移负担（内部 private 方法）。
- `xtp_service/api/trader_service.py::_safe_put`（`trader_service.py:300-304`）— 改为 `_force_wake_all` 内联实现，确保不丢唤醒。

[Classes]

涉及类级别修改的清单如下。

**新增类：**

- `xtp_service/transport/pubsub.py::Subscriber`（dataclass，见 Types）。
- `xtp_service/transport/pubsub.py::BroadcastHub`（管理多订阅者 fan-out，见 Types）。
- `xtp_service/transport/pubsub.py::Broadcaster`（绑定一个事件源 queue + hub + 发送 socket，驱动推送 task）。
- `xtp_service/api/trader_service.py::SubscriberHandle`（封装 queue 与 unsubscribe 回调，供 pubsub 层使用）。

**修改类：**

- `xtp_service/transport/zmq_server.py::ZmqServer`
  - 持有 `self.hubs: dict[str, BroadcastHub]`（key 如 `"trader"`, `"quote"`）。
  - `start()` 启动 `zmq.proxy`（替代 `_proxy_task`）与 `Broadcaster.run()`。
  - `_worker` 区分订阅与普通请求。
- `xtp_service/rpc/registry.py::RpcRegistry`
  - `_handlers` 的 value 由 `fn` 升级为 `HandlerEntry(fn, streaming, subscription)` 或并行维护 `_meta` 字典，`is_on_subscribe` 据此返回。
- `xtp_service/rpc/handlers.py` 中的各闭包 handler：订阅类不再 `while True: await q.get()`，改为一次性登记。
- `xtp_service/api/trader_service.py::TraderService`
  - `subscribe_events` 返回 `SubscriberHandle`；`_event_subscribers` 存 `SubscriberHandle` 而非裸 queue。
  - 新增 `_force_wake_all`。
- `xtp_service/api/quote_service.py::QuoteService`
  - 同 trader，`subscribe_market_data` 按 exchange 分组；`stop` 强制唤醒。
- `xtp_service/protocol/codec.py`：增加 `RpcErrorCode` 常量类。
- `xtp_service/config.py::ZmqServerConfig`：增加字段。

**删除类：** 无。

[Dependencies]

- **无新增第三方依赖**。`pyzmq >= 25.0` 已内置 `zmq.proxy` / `zmq.device`、`zmq.IPV6`、`zmq.ROUTER_MANDATORY` 等所需能力，无需升级版本。
- `msgpack >= 1.0` 已支持 `default=` 钩子，无需升级。
- `uvloop`（Linux）保持不变，新代码全部 asyncio 兼容。
- `pyproject.toml` 无需修改依赖清单；可选在 `[tool.poetry.dependencies]` 显式注明 `pyzmq >= 25.0`（已满足）。
- 仅在 dev group 中已有 `pytest` / `pytest-asyncio`，新测试沿用，无新增。

[Testing]

测试策略遵循「不依赖 XTP 原生库、可在 macOS 跑」的现有约定。

**新增测试文件：**

- `tests/test_pubsub.py`
  - `test_broadcast_hub_subscribe_publish`：单订阅者收帧。
  - `test_broadcast_hub_multi_subscriber`：多订阅者都收到。
  - `test_broadcast_hub_drop_oldest_on_full`：队列满时丢最旧帧、计数 +1。
  - `test_broadcast_hub_unsubscribe_stops_delivery`：unsubscribe 后不再收。
  - `test_broadcaster_run_sends_frames_with_identity`：模拟一个 source queue + 假 socket，验证发送的 multipart 帧格式为 `[identity, req_id, body]`。
- `tests/test_error_frames.py`
  - `test_method_not_found_returns_error_field`：调用未注册方法，客户端 `call()` 返回的对象含 `error.code == -32601`，且**不**含 `result`。
  - `test_dispatch_exception_returns_error_field`：注册一个会抛异常的 handler，验证错误帧走 `error` 字段（修复 `zmq_server.py:177` 的 `result=` 包装 bug）。
  - `test_invalid_params_for_cancel_order`：`trader.cancel_order` 不带 `order_xtp_id`，返回 `INVALID_PARAMS`。
- `tests/test_query_timeout.py`
  - `test_do_query_timeout_when_no_callback`：用一个假的 TraderApi（不回调），验证 `_do_query` 在 `query_timeout` 后 yield 超时错误帧并退出，不泄漏协程（用 `asyncio.all_tasks()` 断言）。
- `tests/test_zmq_loopback.py`（扩展现有）
  - `test_subscription_does_not_block_worker`：订阅一个长流，并发发起 `ping`，断言 ping 在 1s 内返回（证明 worker 未被占满）。
  - `test_high_hwm_under_burst`：突发 5000 帧流式响应，HWM=4096 下不丢帧（或按策略可控丢弃）。

**现有测试修改：**

- `tests/test_protocol.py`：新增 `test_encode_error_helper`、`test_unpack_request_missing_method_raises_rpcerror`。
- `tests/test_zmq_loopback.py::test_streaming_frames`：保持；但 `register_handlers(trader=None, quote=None)` 不变，新测试用独立 fixture。

**回归验证：**

- `poetry run pytest tests/ -v` 必须全绿（macOS 本地）。
- 容器内集成验证（人工）：`scripts/run_server.py` 启动后用 `scripts/test_zmq_client.py` 验 ping、用 `scripts/test_zmq_ipc_client.py` 验 IPC 模式。
- 性能基准（可选）：用 `tests/test_zmq_loopback.py` 中的流式测试，对比修改前后单 worker 每秒帧数，预期 `zmq.proxy` 替换后吞吐提升 2–5x。

[Implementation Order]

按依赖关系与风险递增的顺序实施，每一步都保持测试可跑、回归可控。

1. **协议层基础（低风险，先打底）**
   - `xtp_service/protocol/codec.py`：新增 `RpcErrorCode`、`encode_error`，修 `unpack_request` 容错。
   - `tests/test_protocol.py`：补对应单测。
   - 跑测试确认绿。

2. **配置层（为后续提供参数）**
   - `xtp_service/config.py`：`ZmqServerConfig` 增加 `hwm` / `pubsub_maxsize` / `query_timeout` / `enable_ipv6`，`load_settings` 同步。
   - 不动业务，先合入。

3. **接口层正确性 bug（关键 bug，中等风险）**
   - `xtp_service/api/trader_service.py`：`_do_query` 加超时；`stop` 强制唤醒；新增 `_force_wake_all`、`SubscriberHandle`。
   - `xtp_service/api/quote_service.py`：`subscribe_market_data` 按 exchange 分组；`stop` 强制唤醒。
   - `xtp_service/rpc/handlers.py`：`cancel_order` / `insert_order` 参数校验。
   - `tests/test_query_timeout.py`：新增。
   - 跑测试。

4. **pubsub 解耦（关键 bug，新模块）**
   - 新建 `xtp_service/transport/pubsub.py`：`Subscriber` / `BroadcastHub` / `Broadcaster`。
   - `tests/test_pubsub.py`：单测。
   - `xtp_service/rpc/registry.py`：`register` 增加 `subscription` 元信息，`is_on_subscribe`。
   - `xtp_service/rpc/handlers.py`：`trader.subscribe_events` / `quote.subscribe_events` 改为登记 + ack。
   - 跑测试。

5. **通信层拓扑重构（性能 + 正确性，最高风险，放最后）**
   - `xtp_service/transport/zmq_server.py`：
     a. 用 `zmq.proxy` 替换 `_proxy_task`；
     b. HWM 用 `cfg.hwm`；
     c. 修错误帧编码 bug（`_handle_request` 内 `error` 分支）；
     d. `_worker` 区分订阅请求 → 委托 `_handle_subscription`；
     e. `start`/`stop` 启停 `Broadcaster`，优雅排空。
   - `scripts/run_server.py`：注入 `Broadcaster`，调整关闭顺序。
   - 扩展 `tests/test_zmq_loopback.py`：订阅不阻塞 worker、错误帧字段、突发背压。
   - `tests/test_error_frames.py`：新增。
   - 全量回归 `poetry run pytest tests/ -v`。

6. **客户端可选增强（低风险，向后兼容）**
   - `client/zmq_client.py`：`setsockopt(42,1)` → `setsockopt(zmq.IPV6, ...)`；`enable_ipv6` 构造参数；`close` 幂等；`subscribe` 超时日志。
   - 更新 `README.md` 中 `enable_ipv6` 说明与 RPC 方法清单（标注订阅类方法的语义变化）。

7. **文档与收尾**
   - `README.md`：更新协议帧格式说明（错误帧走 `error` 字段）、`ZmqServerConfig` 新字段、订阅行为说明。
   - `docs/`：新增 `PERFORMANCE_AUDIT.md` 记录本次发现与修复项，便于复盘。