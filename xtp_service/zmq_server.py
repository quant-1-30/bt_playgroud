from __future__ import annotations

import asyncio
import logging
import time

import zmq
import zmq.asyncio

from collections import defaultdict, deque
from xtp_service.config import ZmqServerConfig
from xtp_service.protocol import EOF, RpcErrorCode, RpcError, encode_error, encode_response, unpack_request
from xtp_service.rpc.registry import rpc_registry
from xtp_service.transport.pubsub import BroadcastHub

log = logging.getLogger(__name__)


class ZmqServer:
    """ZMQ ROUTER frontend + DEALER backend + N workers.

    Uses zmq.proxy (C-level) for frontend<->backend forwarding.
    Subscription requests are detected via registry.is_on_subscribe()
    and handled by _handle_subscription (non-blocking, uses BroadcastHub).
    """

    def __init__(self, cfg: ZmqServerConfig) -> None:
        self.cfg = cfg
        if cfg.ipc_path:
            self.frontend_url = f"ipc://{cfg.ipc_path}"
            self.backend_url = f"ipc://{cfg.ipc_path}.backend"
        else:
            self.frontend_url = f"tcp://{cfg.host}:{cfg.port}"
            self.backend_url = f"tcp://{cfg.host}:{cfg.backend_port}"
        self.loop = None
        self.context = None
        self.frontend = None
        self.backend = None
        self._proxy_ctx = None
        self._proxy_frontend = None
        self._proxy_backend = None
        self._proxy_task = None
        self.worker_tasks = []
        self.shutdown_event = asyncio.Event()
        self.total_requests = 0
        self.dropped_requests = 0
        self.active_connections = defaultdict(
            lambda: {"last_seen": time.time(), "rate_limiter": deque(), "request_count": 0}
        )
        self.cleanup_interval = 30
        self.connection_timeout = 300
        self.hubs = {}

    def setup_hub(self, method, maxsize=0):
        hub = BroadcastHub(maxsize=maxsize or self.cfg.pubsub_maxsize)
        self.hubs[method] = hub
        return hub

    def _start_proxy(self):
        self._proxy_ctx = zmq.Context.instance()
        self._proxy_frontend = self._proxy_ctx.socket(zmq.ROUTER)
        self._proxy_frontend.set_hwm(self.cfg.hwm)
        self._proxy_frontend.bind(self.frontend_url)
        self._proxy_backend = self._proxy_ctx.socket(zmq.DEALER)
        self._proxy_backend.set_hwm(self.cfg.hwm)
        self._proxy_backend.bind(self.backend_url)

        async def _run():
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, zmq.proxy, self._proxy_frontend, self._proxy_backend)
            log.info("zmq.proxy exited")

        self._proxy_task = asyncio.get_running_loop().create_task(_run())

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.context = zmq.asyncio.Context()
        self.frontend = self.context.socket(zmq.ROUTER)
        self.frontend.set_hwm(self.cfg.hwm)
        for i in range(self.cfg.max_workers):
            self.worker_tasks.append(self.loop.create_task(self._worker(i)))
        self.worker_tasks.append(self.loop.create_task(self._cleanup_connections()))
        self._start_proxy()
        log.info("ZMQ server listening on %s with %d workers (HWM=%d)", self.frontend_url, self.cfg.max_workers, self.cfg.hwm)
        try:
            await self.shutdown_event.wait()
        except asyncio.CancelledError:
            await self.stop()

    async def stop(self):
        log.info("Stopping ZMQ server...")
        self.shutdown_event.set()
        if self._proxy_frontend:
            try: self._proxy_frontend.close(0)
            except: pass
        if self._proxy_backend:
            try: self._proxy_backend.close(0)
            except: pass
        if self._proxy_task and not self._proxy_task.done():
            try: await asyncio.wait_for(self._proxy_task, timeout=2.0)
            except: self._proxy_task.cancel()
        for task in self.worker_tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        if self.frontend: self.frontend.close()
        if self.backend: self.backend.close()
        if self.context: self.context.term()
        log.info("ZMQ server stopped")

    async def _worker(self, worker_id):
        assert self.context is not None
        ws = self.context.socket(zmq.DEALER)
        ws.set_hwm(self.cfg.hwm)
        ws.connect(self.backend_url)
        log.info("Worker %d started", worker_id)
        while not self.shutdown_event.is_set():
            try:
                identity, req_id, payload = await ws.recv_multipart()
                try:
                    request = unpack_request(payload)
                except RpcError as e:
                    await ws.send_multipart([identity, req_id, encode_error(e.code, e.message)])
                    await ws.send_multipart([identity, req_id, EOF])
                    continue
                except Exception as e:
                    await ws.send_multipart([identity, req_id, encode_error(RpcErrorCode.PARSE_ERROR, f"parse error: {e}")])
                    await ws.send_multipart([identity, req_id, EOF])
                    continue

                if rpc_registry.is_on_subscribe(request.method) and request.method in self.hubs:
                    await self._handle_subscription(identity, req_id, request.method, ws)
                else:
                    await self._handle_request(identity, req_id, request, ws)
            except asyncio.CancelledError: break
            except zmq.ZMQError as ze:
                if ze.errno == zmq.ETERM: break
            except Exception as e: log.warning("Worker %d error: %s", worker_id, e)
        try: ws.close()
        except: pass
        log.info("Worker %d stopped", worker_id)

    async def _handle_subscription(self, identity, req_id, method, ws):
        """Register subscriber to hub and return ack immediately (non-blocking)."""
        hub = self.hubs.get(method)
        if hub is None:
            await ws.send_multipart([identity, req_id, encode_error(RpcErrorCode.METHOD_NOT_FOUND, f"no hub for {method}")])
            await ws.send_multipart([identity, req_id, EOF])
            return
        hub.subscribe(identity, req_id)
        await ws.send_multipart([identity, req_id, encode_response(result={"subscribed": True, "method": method})])
        await ws.send_multipart([identity, req_id, EOF])
        log.info("Subscription registered: method=%s identity=%s (worker freed)", method, identity[:16])

    async def _handle_request(self, identity, req_id, request, ws):
        conn = self.active_connections[identity]
        conn["last_seen"] = time.time()
        conn["request_count"] += 1
        self.total_requests += 1
        if self.cfg.rate_limit_per_minute and not self._check_limit(identity, self.cfg.rate_limit_per_minute):
            self.dropped_requests += 1
            await ws.send_multipart([identity, req_id, encode_error(RpcErrorCode.RATE_LIMITED, "rate limit exceeded")])
            await ws.send_multipart([identity, req_id, EOF])
            return
        count = 0
        try:
            async for frame in rpc_registry.dispatch(request):
                if isinstance(frame, dict) and "error" in frame:
                    body = encode_response(error=frame["error"])
                elif isinstance(frame, (bytes, bytearray)):
                    body = frame
                else:
                    body = encode_response(result=frame)
                await ws.send_multipart([identity, req_id, body])
                count += 1
                if count % 127 == 0: await asyncio.sleep(0)
        except Exception as e:
            log.exception("RPC dispatch failed for %s: %s", request.method, e)
            await ws.send_multipart([identity, req_id, encode_error(RpcErrorCode.INTERNAL_ERROR, f"{type(e).__name__}: {e}")])
        await ws.send_multipart([identity, req_id, EOF])

    def _check_limit(self, addr, max_per_min):
        now = time.time()
        rl = self.active_connections[addr]["rate_limiter"]
        while rl and rl[0] < now - 60: rl.popleft()
        if len(rl) >= max_per_min: return False
        rl.append(now)
        return True

    async def _cleanup_connections(self):
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(self.cleanup_interval)
                now = time.time()
                inactive = [cid for cid, info in self.active_connections.items() if now - info["last_seen"] > self.connection_timeout]
                for cid in inactive: self.active_connections.pop(cid, None)
                log.info("Stats: active=%d, processed=%d, dropped=%d", len(self.active_connections), self.total_requests, self.dropped_requests)
            except asyncio.CancelledError: break
            except Exception as e: log.warning("Connection cleanup error: %s", e)
