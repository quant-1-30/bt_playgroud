from __future__ import annotations

import asyncio
import logging
import time

import zmq
import zmq.asyncio

from collections import defaultdict, deque
from ..config import ZmqServerConfig
from ..protocol.codec import EOF, encode_response, unpack_request
from ..rpc.registry import rpc_registry

log = logging.getLogger(__name__)


class ZmqServer:
    """ZMQ ROUTER """

    def __init__(self, cfg: ZmqServerConfig) -> None:
        self.cfg = cfg
        # 支持 IPC 或 TCP
        if cfg.ipc_path:
            self.frontend_url = f"ipc://{cfg.ipc_path}"
            self.backend_url = f"ipc://{cfg.ipc_path}.backend"
        else:
            self.frontend_url = f"tcp://{cfg.host}:{cfg.port}"
            self.backend_url = f"tcp://{cfg.host}:{cfg.backend_port}"
        self.loop: asyncio.AbstractEventLoop | None = None
        self.context: zmq.asyncio.Context | None = None
        self.frontend: zmq.asyncio.Socket | None = None
        self.backend: zmq.asyncio.Socket | None = None
        self.worker_tasks: list[asyncio.Task] = []
        self.shutdown_event = asyncio.Event()

        self.total_requests = 0
        self.dropped_requests = 0
        self.active_connections: dict[bytes, dict] = defaultdict(
            lambda: {"last_seen": time.time(), "rate_limiter": deque(), "request_count": 0}
        )
        self.cleanup_interval = 30
        self.connection_timeout = 300

    # ------------------------------------------------------------------
    # lifetime
    # ------------------------------------------------------------------
    def _create_sockets(self) -> None:
        assert self.context is not None
        self.frontend = self.context.socket(zmq.ROUTER)
        self.frontend.set_hwm(1000)
        self.frontend.bind(self.frontend_url)

        self.backend = self.context.socket(zmq.DEALER)
        self.backend.set_hwm(1000)
        self.backend.bind(self.backend_url)

    async def start(self) -> None:
        """server shutdown_event """
        self.loop = asyncio.get_running_loop()
        self.context = zmq.asyncio.Context()
        self._create_sockets()

        for i in range(self.cfg.max_workers):
            self.worker_tasks.append(self.loop.create_task(self._worker(i)))
        self.worker_tasks.append(self.loop.create_task(self._cleanup_connections()))
        self.worker_tasks.append(self.loop.create_task(self._proxy_task()))

        log.info("ZMQ server listening on %s with %d workers", self.frontend_url, self.cfg.max_workers)
        try:
            await self.shutdown_event.wait()
        except asyncio.CancelledError:
            log.info("ZMQ server cancelled, stopping...")
            await self.stop()

    async def stop(self) -> None:
        """server shutdown socket and worker"""
        log.info("Stopping ZMQ server...")
        self.shutdown_event.set()
        for task in self.worker_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)

        if self.frontend:
            self.frontend.close()
        if self.backend:
            self.backend.close()
        if self.context:
            self.context.term()

    # ------------------------------------------------------------------
    # frontend <-> backend
    # ------------------------------------------------------------------
    async def _proxy_task(self) -> None:
        """用 poller 在 frontend/backend 之间转发多帧消息。"""
        assert self.frontend is not None and self.backend is not None
        poller = zmq.asyncio.Poller()
        poller.register(self.frontend, zmq.POLLIN)
        poller.register(self.backend, zmq.POLLIN)

        while not self.shutdown_event.is_set():
            try:
                events = await poller.poll(timeout=1000)
                for socket, event in events:
                    if event != zmq.POLLIN:
                        continue
                    msg = await socket.recv_multipart()
                    if socket == self.frontend:
                        await self.backend.send_multipart(msg)
                    else:
                        await self.frontend.send_multipart(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                log.warning("Proxy polling error: %s", e)
                await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # worker backend rpc_registry
    # ------------------------------------------------------------------
    async def _worker(self, worker_id: int) -> None:
        assert self.context is not None
        worker_socket = self.context.socket(zmq.DEALER)
        worker_socket.connect(self.backend_url)
        log.info("Worker %d started", worker_id)

        while not self.shutdown_event.is_set():
            try:
                # backend 转发过来的帧：[identity, req_id, payload]
                identity, req_id, payload = await worker_socket.recv_multipart()
                await self._handle_request(identity, req_id, payload, worker_socket)
            except asyncio.CancelledError:
                break
            except zmq.ZMQError as ze:
                if ze.errno == zmq.ETERM:
                    break
            except Exception as e:  # noqa: BLE001
                log.warning("Worker %d error: %s", worker_id, e)

        worker_socket.close()
        log.info("Worker %d stopped", worker_id)

    async def _handle_request(
        self,
        identity: bytes,
        req_id: bytes,
        payload: bytes,
        worker_socket: zmq.asyncio.Socket,
    ) -> None:
        # limit-rate
        conn = self.active_connections[identity]
        conn["last_seen"] = time.time()
        conn["request_count"] += 1
        self.total_requests += 1
        if self.cfg.rate_limit_per_minute and not self._check_limit(identity, self.cfg.rate_limit_per_minute):
            self.dropped_requests += 1
            await worker_socket.send_multipart(
                [identity, req_id, encode_response(error={"code": -32004, "message": "rate limit exceeded"})]
            )
            await worker_socket.send_multipart([identity, req_id, EOF])
            return

        try:
            request = unpack_request(payload)
        except Exception as e:  # noqa: BLE001
            await worker_socket.send_multipart(
                [identity, req_id, encode_response(error={"code": -32700, "message": f"parse error: {e}"})]
            )
            await worker_socket.send_multipart([identity, req_id, EOF])
            return

        count = 0
        try:
            async for frame in rpc_registry.dispatch(request):
                # frame 为 dict 或已编码 bytes；统一编码
                body = frame if isinstance(frame, (bytes, bytearray)) else encode_response(result=frame)
                await worker_socket.send_multipart([identity, req_id, body])
                count += 1
                if count % 127 == 0:
                    await asyncio.sleep(0)
        except Exception as e:  # noqa: BLE001
            log.exception("RPC dispatch failed for %s: %s", request.method, e)
            await worker_socket.send_multipart(
                [identity, req_id, encode_response(error={"code": -32000, "message": f"{type(e).__name__}: {e}"})]
            )

        # eof
        await worker_socket.send_multipart([identity, req_id, EOF])

    # ------------------------------------------------------------------
    # limit
    # ------------------------------------------------------------------
    def _check_limit(self, addr: bytes, max_requests_per_minute: int) -> bool:
        now = time.time()
        rl = self.active_connections[addr]["rate_limiter"]
        while rl and rl[0] < now - 60:
            rl.popleft()
        if len(rl) >= max_requests_per_minute:
            return False
        rl.append(now)
        return True

    async def _cleanup_connections(self) -> None:
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(self.cleanup_interval)
                now = time.time()
                inactive = [cid for cid, info in self.active_connections.items()
                            if now - info["last_seen"] > self.connection_timeout]
                for cid in inactive:
                    self.active_connections.pop(cid, None)
                log.info(
                    "Stats: active=%d, processed=%d, dropped=%d",
                    len(self.active_connections), self.total_requests, self.dropped_requests,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                log.warning("Connection cleanup error: %s", e)
