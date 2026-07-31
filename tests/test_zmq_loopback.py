from __future__ import annotations

import asyncio
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtp_service.config import ZmqServerConfig  # noqa: E402
from xtp_service.rpc.handlers import register_handlers  # noqa: E402
from xtp_service.rpc.registry import rpc_registry  # noqa: E402
from xtp_service.transport.zmq_server import ZmqServer  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
from zmq_client import XtpClient  # noqa: E402


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _start_server(port: int) -> ZmqServer:
    """ping handler and server trader/quote"""
    rpc_registry._handlers.clear()
    register_handlers(trader=None, quote=None)

    cfg = ZmqServerConfig(host="127.0.0.1", port=port, backend_port=port + 1, max_workers=2)
    server = ZmqServer(cfg)
    task = asyncio.create_task(server.start())
    # server wait to bind
    await asyncio.sleep(0.3)
    server._test_task = task  # type: ignore[attr-defined]
    return server


@pytest.mark.asyncio
async def test_ping_pong():
    port = _free_port()
    server = await _start_server(port)
    client = XtpClient(f"tcp://127.0.0.1:{port}", timeout=5.0)
    try:
        await client.connect()
        result = await asyncio.wait_for(client.ping(), timeout=5.0)
        assert result["result"]["pong"] is True
    finally:
        await client.close()
        await server.stop()
        if getattr(server, "_test_task", None):
            server._test_task.cancel()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_streaming_frames():
    """handler yield frame and client.subscribe"""
    port = _free_port()
    rpc_registry._handlers.clear()
    register_handlers(trader=None, quote=None)

    @rpc_registry.register("test.stream")
    async def _stream(params):
        for i in range(3):
            yield {"i": i}

    cfg = ZmqServerConfig(host="127.0.0.1", port=port, backend_port=port + 1, max_workers=2)
    server = ZmqServer(cfg)
    task = asyncio.create_task(server.start())
    await asyncio.sleep(0.3)
    client = XtpClient(f"tcp://127.0.0.1:{port}", timeout=5.0)
    try:
        await client.connect()
        frames = []
        async for f in client.subscribe("test.stream", {}):
            frames.append(f)
        assert len(frames) == 3
        assert frames[0]["result"] == {"i": 0}
        assert frames[2]["result"] == {"i": 2}
    finally:
        await client.close()
        await server.stop()
        task.cancel()