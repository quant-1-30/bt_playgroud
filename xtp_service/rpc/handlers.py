from __future__ import annotations

from typing import Any, AsyncIterator

from .registry import rpc_registry


def register_handlers(trader: Any, quote: Any) -> None:
    # ------------------------------------------------------------------
    # requry
    # ------------------------------------------------------------------
    if trader is not None:
        @rpc_registry.register("trader.query_asset")
        async def _query_asset(params: dict) -> AsyncIterator[dict]:
            async for frame in trader.query_asset():
                yield frame

        @rpc_registry.register("trader.query_position")
        async def _query_position(params: dict) -> AsyncIterator[dict]:
            ticker = params.get("ticker", "")
            async for frame in trader.query_position(ticker):
                yield frame

        @rpc_registry.register("trader.query_order")
        async def _query_order(params: dict) -> AsyncIterator[dict]:
            req = params.get("req", {})
            async for frame in trader.query_order(req):
                yield frame

        @rpc_registry.register("trader.query_trade")
        async def _query_trade(params: dict) -> AsyncIterator[dict]:
            req = params.get("req", {})
            async for frame in trader.query_trade(req):
                yield frame

        @rpc_registry.register("trader.query_account_trade_market")
        async def _q_market(params: dict) -> AsyncIterator[dict]:
            async for frame in trader.query_account_trade_market():
                yield frame

        # ------------------------------------------------------------------
        # trade set / withdraw
        # ------------------------------------------------------------------
        @rpc_registry.register("trader.insert_order")
        def _insert_order(params: dict) -> dict:
            return trader.insert_order(params.get("req", {}))

        @rpc_registry.register("trader.cancel_order")
        def _cancel_order(params: dict) -> dict:
            return trader.cancel_order(int(params["order_xtp_id"]))

        # ------------------------------------------------------------------
        # trade subscribe
        # ------------------------------------------------------------------
        @rpc_registry.register("trader.subscribe_events")
        async def _sub_events(params: dict) -> AsyncIterator[dict]:
            q = trader.subscribe_events()
            try:
                while True:
                    frame = await q.get()
                    if frame is None:
                        return
                    yield frame
            finally:
                trader.unsubscribe_events(q)

    # ------------------------------------------------------------------
    # subscribe quote
    # ------------------------------------------------------------------
    if quote is not None:
        @rpc_registry.register("quote.subscribe_market_data")
        def _sub_md(params: dict) -> dict:
            return quote.subscribe_market_data(params.get("tickers", []))

        @rpc_registry.register("quote.unsubscribe_market_data")
        def _unsub_md(params: dict) -> dict:
            return quote.unsubscribe_market_data(params.get("tickers", []))

        @rpc_registry.register("quote.subscribe_events")
        async def _quote_events(params: dict) -> AsyncIterator[dict]:
            q = quote.subscribe_events()
            try:
                while True:
                    frame = await q.get()
                    if frame is None:
                        return
                    yield frame
            finally:
                quote.unsubscribe_events(q)

    # ------------------------------------------------------------------
    # health check
    # ------------------------------------------------------------------
    @rpc_registry.register("ping")
    def _ping(params: dict) -> dict:
        return {
            "pong": True,
            "trader_started": trader is not None and trader.started,
            "quote_started": quote is not None and quote.started,
        }