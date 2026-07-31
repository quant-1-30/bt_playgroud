from __future__ import annotations

import asyncio
import logging
import signal
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtp_service.api import load_native_libs  # noqa: E402
from xtp_service.api.quote_service import QuoteService  # noqa: E402
from xtp_service.api.trader_service import TraderService  # noqa: E402
from xtp_service.config import load_settings  # noqa: E402
from xtp_service.rpc.handlers import register_handlers  # noqa: E402
from xtp_service.transport.zmq_server import ZmqServer  # noqa: E402

log = logging.getLogger("xtp-server")


async def _run() -> None:
    settings = load_settings()

    load_native_libs(settings.native_dir)

    trader: TraderService | None = None
    quote: QuoteService | None = None
    loop = asyncio.get_running_loop()

    if settings.trader.user:
        trader = TraderService(settings.trader)
        await trader.start(loop)
    if settings.quote.user:
        quote = QuoteService(settings.quote)
        await quote.start(loop)

    register_handlers(trader, quote)
    log.info("Registered RPC methods: %s", __import__("xtp_service.rpc.registry", fromlist=["rpc_registry"]).rpc_registry.methods())

    server = ZmqServer(settings.zmq)

    async def _shutdown() -> None:
        log.info("Shutting down...")
        await server.stop()
        if trader:
            await trader.stop()
        if quote:
            await quote.stop()

    loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(_shutdown()))
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(_shutdown()))

    try:
        await server.start()
    finally:
        if trader:
            await trader.stop()
        if quote:
            await quote.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()