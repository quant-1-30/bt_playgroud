from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtp_service.config import ZmqServerConfig  # noqa: E402
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


async def test_subscription_does_not_block_worker():
    port = _free_port()
    rpc_registry._handlers.clear()
    register_handlers(trader=None, quote=None)

    @rpc_registry.register("test.long_stream")
    async def _ls(params):
        for i in range(1000):
            yield {"i": i}
            await asyncio.sleep(0.01)

    cfg = ZmqServerConfig(host="127.0.0.1", port=port, backend_port=port + 1, max_workers=2)
    server = ZmqServer(cfg)
    task = asyncio.create_task(server.start())
    await asyncio.sleep(0.5)
    client = XtpClient(f"tcp://127.0.0.1:{port}", timeout=5.0)
    try:
        await client.connect()
        got = []

        async def _collect():
            async for f in client.subscribe("test.long_stream", {}):
                got.append(f)
                if len(got) >= 3: break

        st = asyncio.create_task(_collect())
        await asyncio.sleep(0.3)
        pr = await asyncio.wait_for(client.call("ping", {}), timeout=3.0)
        assert pr["result"]["pong"] is True
        st.cancel()
        try: await st
        except asyncio.CancelledError: pass
    finally:
        await client.close()
        await server.stop()
        task.cancel()


async def test_high_hwm_under_burst():
    port = _free_port()
    rpc_registry._handlers.clear()
    register_handlers(trader=None, quote=None)

    @rpc_registry.register("test.burst")
    async def _b(params):
        for i in range(5000):
            yield {"i": i}

    cfg = ZmqServerConfig(host="127.0.0.1", port=port, backend_port=port + 1, max_workers=2, hwm=4096)
    server = ZmqServer(cfg)
    task = asyncio.create_task(server.start())
    await asyncio.sleep(0.5)
    client = XtpClient(f"tcp://127.0.0.1:{port}", timeout=15.0)
    try:
        await client.connect()
        c = 0
        async for f in client.subscribe("test.burst", {}):
            c += 1
        assert c > 4000, f"Only {c} frames"
    finally:
        await client.close()
        await server.stop()
        task.cancel()
