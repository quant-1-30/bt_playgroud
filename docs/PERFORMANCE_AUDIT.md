# 性能审计与修复记录

> 本文档记录了对 `bt_playgroud` 仓库的第三方接口（XTP Trader/Quote API）+ 交易通信组件（ZMQ ROUTER/DEALER RPC + msgpack）的性能卡点与逻辑 bug 的全面审计发现与修复项。

## 审计概要

共发现 **12 处** 问题，其中 **3 处关键正确性 bug**（P0）会直接导致交易/行情不可用或客户端误判成功。

## P0 关键正确性 Bug（3 处）

### Bug #1：订阅阻塞 worker pool

- **文件**：`xtp_service/rpc/handlers.py` — `trader.subscribe_events` / `quote.subscribe_events`
- **现象**：handler 内部 `while True: await q.get()` 长循环，每个订阅者占用一个 worker。8 个订阅者即耗尽 worker pool → 服务完全卡死。
- **修复**：
  - 新建 `xtp_service/transport/pubsub.py`（`BroadcastHub` + `Broadcaster`）解耦长连接订阅
  - `register_handlers` 中用 `subscription=True` 标记订阅方法
  - worker 只做「登记 + ack」立即返回，不再阻塞

### Bug #2：XTP 查询无超时

- **文件**：`xtp_service/api/trader_service.py` — `_do_query` 的 `await q.get()`
- **现象**：C++ SPI 不回调时协程永久挂起、worker 泄漏，最终服务不可用
- **修复**：`asyncio.wait_for(q.get(), timeout=cfg.query_timeout)`，超时 yield `QUERY_TIMEOUT`(-32010) 错误帧并退出

### Bug #3：错误帧被包装成 result

- **文件**：`xtp_service/transport/zmq_server.py` — `_handle_request` 第 177 行
- **现象**：dispatch 产出的 `{"error": {...}}` dict 走 `encode_response(result=frame)` → 错误被包成 `{result: {error: ...}}`，客户端无法识别失败
- **修复**：检查 frame 含 `error` 键时走 `encode_response(error=frame["error"])`

## P1 重要 Bug（5 处）

### Bug #4：cancel_order KeyError

- **文件**：`xtp_service/rpc/handlers.py:50`
- **现象**：`params["order_xtp_id"]` 缺失时抛 `KeyError`，客户端收到 INTERNAL_ERROR 而非 INVALID_PARAMS
- **修复**：校验 `params.get("order_xtp_id")`，缺失/类型错误返回 `INVALID_PARAMS`(-32602)

### Bug #5：subscribe_market_data 只用第一个 exchange

- **文件**：`xtp_service/api/quote_service.py:84-86`
- **现象**：`eid = tl[0].get("exchange_id", 2)` 忽略列表中其它交易所的标的
- **修复**：按 `exchange_id` 分组分别调用 `subscribeMarketData`，聚合返回值

### Bug #6：stop() 的 _safe_put 静默丢消息

- **文件**：`xtp_service/api/trader_service.py:174-177` / `quote_service.py:71-73`
- **现象**：队列满时 `put_nowait` 抛 `QueueFull` 被 `except: pass` 吞掉 → 消费者协程永远不被唤醒
- **修复**：新增 `_force_wake_all()` + `_safe_force_put()`，队列满时丢弃最旧元素后重试

### Bug #7：setsockopt(42, 1) 魔术值

- **文件**：`client/zmq_client.py:28`
- **现象**：`setsockopt(42, 1)` 中 42 是 `ZMQ_IPV6` 的魔术值，不可读且默认强制开 IPv6
- **修复**：改为 `setsockopt(zmq.IPV6, 1 if enable_ipv6 else 0)`，新增 `enable_ipv6` 构造参数（默认 False）

### Bug #8：insert_order 无参数校验

- **文件**：`xtp_service/rpc/handlers.py:45`
- **现象**：`params.get("req", {})` 不校验类型，传入非 dict 时可能导致 XTP C++ 层段错误
- **修复**：校验 `req` 为 dict，否则返回 `INVALID_PARAMS`(-32602)

## P2 性能优化（4 处）

### Opt #1：zmq.proxy 替换 Python poller

- **文件**：`xtp_service/transport/zmq_server.py`
- **变更**：删除 `_proxy_task` 的 Python `zmq.asyncio.Poller` 实现，改用 C 级 `zmq.proxy(frontend, backend)` 在 `run_in_executor` 线程中运行
- **收益**：消除每帧两次 Python/C 边界穿越，吞吐提升预期 2–5x

### Opt #2：HWM 从 1000 提升到 4096

- **文件**：`xtp_service/transport/zmq_server.py` + `client/zmq_client.py`
- **变更**：`set_hwm(1000)` → `set_hwm(cfg.hwm)`（默认 4096）；client 端也同步提升
- **收益**：突发流量（如 5000 帧流式查询）不再因 HWM 过低丢帧

### Opt #3：统一错误码

- **文件**：`xtp_service/protocol/codec.py`
- **变更**：新增 `RpcErrorCode` 常量类（JSON-RPC 2.0 兼容）+ `encode_error()` 便捷函数
- **收益**：消除分散在代码各处的硬编码错误码

### Opt #4：unpack_request 容错

- **文件**：`xtp_service/protocol/codec.py`
- **变更**：`method` 缺失或 msgpack 解码失败时抛 `RpcError(PARSE_ERROR)` 而非 `KeyError`/`Exception`
- **收益**：客户端收到结构化错误帧而非连接断开

## 修复文件清单

| 文件 | 变更类型 |
|---|---|
| `xtp_service/protocol/codec.py` | 新增 RpcErrorCode / RpcError / encode_error / unpack_request 容错 |
| `xtp_service/config.py` | ZmqServerConfig 新增 hwm/pubsub_maxsize/query_timeout/enable_ipv6 |
| `xtp_service/api/trader_service.py` | _do_query 超时 / _force_wake_all / SubscriberHandle |
| `xtp_service/api/quote_service.py` | subscribe_market_data 按 exchange 分组 / _force_wake_all |
| `xtp_service/rpc/registry.py` | HandlerEntry / is_on_subscribe / dispatch_stream / RpcError 处理 |
| `xtp_service/rpc/handlers.py` | 参数校验 / subscription=True 标记 |
| `xtp_service/transport/pubsub.py` | **新建** BroadcastHub / Broadcaster |
| `xtp_service/transport/zmq_server.py` | zmq.proxy / HWM / 错误帧 fix / encode_error |
| `client/zmq_client.py` | zmq.IPV6 / enable_ipv6 / close 幂等 / subscribe 超时日志 |
| `scripts/run_server.py` | query_timeout 注入 / 关闭顺序 |
| `README.md` | 更新协议帧格式 / 错误码 / 配置项 / 测试说明 |

## 测试覆盖

| 测试文件 | 覆盖点 |
|---|---|
| `tests/test_protocol.py` | encode_error / unpack_request 容错 / RpcError |
| `tests/test_query_timeout.py` | _do_query 超时不泄漏协程 |
| `tests/test_pubsub.py` | BroadcastHub subscribe/publish/drop/unsubscribe / Broadcaster 帧格式 |
| `tests/test_error_frames.py` | 错误帧走 error 字段 / method not found / handler exception |
| `tests/test_zmq_loopback.py` | ping/pong / 流式多帧（回归） |
| `tests/test_zmq_loopback_ext.py` | 订阅不阻塞 worker / 5000 帧突发背压 |