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
from xtp_service.transport.pubsub import Broadcaster  # noqa: E402
from xtp_service.transport.zmq_server import ZmqServer  # noqa: E402

log = logging.getLogger("xtp-server")


async def _run() -> None:
    settings = load_settings()
    load_native_libs(settings.native_dir)
    trader = None
    quote = None
    broadcasters = []
    loop = asyncio.get_running_loop()

    if settings.trader.user:
        trader = TraderService(settings.trader, query_timeout=settings.zmq.query_timeout)
        await trader.start(loop)
    if settings.quote.user:
        quote = QuoteService(settings.quote)
        await quote.start(loop)

    register_handlers(trader, quote)
    server = ZmqServer(settings.zmq)

    # Setup pubsub: hub + broadcaster for each subscription method
    if trader is not None:
        trader_hub = server.setup_hub("trader.subscribe_events")
        trader_source_q = trader.subscribe_events()
        bc = Broadcaster(trader_source_q, trader_hub, server.frontend, "trader")
        broadcasters.append(bc)

    if quote is not None:
        quote_hub = server.setup_hub("quote.subscribe_events")
        quote_source_q = quote.subscribe_events()
        bc2 = Broadcaster(quote_source_q, quote_hub, server.frontend, "quote")
        broadcasters.append(bc2)

    # Start broadcasters
    for bc in broadcasters:
        bc.start()

    async def _shutdown():
        log.info("Shutting down...")
        await server.stop()
        for bc in broadcasters:
            await bc.stop()
        if trader: await trader.stop()
        if quote: await quote.stop()

    loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(_shutdown()))
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(_shutdown()))

    try:
        await server.start()
    finally:
        for bc in broadcasters:
            await bc.stop()
        if trader: await trader.stop()
        if quote: await quote.stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
