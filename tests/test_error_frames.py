from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtp_service.config import ZmqServerConfig  # noqa: E402
from xtp_service.protocol.codec import RpcErrorCode  # noqa: E402
from xtp_service.rpc.handlers import register_handlers  # noqa: E402
from xtp_service.rpc.registry import rpc_registry  # noqa: E402
from xtp_service.transport.zmq_server import ZmqServer  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
from zmq_client import XtpClient  # noqa: E402


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def test_method_not_found_returns_error_field():
    port = _free_port()
    rpc_registry._handlers.clear()
    register_handlers(trader=None, quote=None)
    cfg = ZmqServerConfig(host="127.0.0.1", port=port, backend_port=port + 1, max_workers=2)
    server = ZmqServer(cfg)
    task = asyncio.create_task(server.start())
    await asyncio.sleep(0.5)
    client = XtpClient(f"tcp://127.0.0.1:{port}", timeout=5.0)
    try:
        await client.connect()
        result = await asyncio.wait_for(client.call("nonexistent.method", {}), timeout=5.0)
        assert "error" in result
        assert "result" not in result
        assert result["error"]["code"] == RpcErrorCode.METHOD_NOT_FOUND
    finally:
        await client.close()
        await server.stop()
        task.cancel()


async def test_dispatch_exception_returns_error_field():
    port = _free_port()
    rpc_registry._handlers.clear()
    register_handlers(trader=None, quote=None)

    @rpc_registry.register("test.boom")
    def _boom(params):
        raise ValueError("kaboom")

    cfg = ZmqServerConfig(host="127.0.0.1", port=port, backend_port=port + 1, max_workers=2)
    server = ZmqServer(cfg)
    task = asyncio.create_task(server.start())
    await asyncio.sleep(0.5)
    client = XtpClient(f"tcp://127.0.0.1:{port}", timeout=5.0)
    try:
        await client.connect()
        result = await asyncio.wait_for(client.call("test.boom", {}), timeout=5.0)
        assert "error" in result
        assert "result" not in result
        assert result["error"]["code"] == RpcErrorCode.INTERNAL_ERROR
    finally:
        await client.close()
        await server.stop()
        task.cancel()
