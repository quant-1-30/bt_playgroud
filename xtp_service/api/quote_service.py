from __future__ import annotations

import asyncio
import logging

from typing import Any, Optional
from ..config import XtpQuoteConfig
from .base import XtpBaseService, to_serializable

log = logging.getLogger(__name__)


def _make_spi_class(quote_base: type) -> type:
    """XTP QuoteApi SPI and  delegate to QuoteService"""

    class _QuoteSpi(quote_base):  # type: ignore[misc, valid-type]
        _owner: Optional["QuoteService"] = None

        def onDisconnected(self, reason):
            if self._owner:
                log.warning("XTP quote disconnected: reason=%s", reason)
                self._owner._emit_event("onDisconnected", {"reason": reason})

        def onError(self, data):
            if self._owner:
                self._owner._emit_event("onError", None, data)

        def onSubMarketData(self, data, error, last):
            if self._owner:
                self._owner._emit_event("onSubMarketData", data, error)

        def onDepthMarketData(self, data, *args):
            if self._owner:
                self._owner._emit_event("onDepthMarketData", data, None)

        def onSubOrderBook(self, data, error, last):
            if self._owner:
                self._owner._emit_event("onSubOrderBook", data, error)

        def onOrderBook(self, data):
            if self._owner:
                self._owner._emit_event("onOrderBook", data, None)

        def onSubTickByTick(self, data, error, last):
            if self._owner:
                self._owner._emit_event("onSubTickByTick", data, error)

        def onTickByTick(self, data):
            if self._owner:
                self._owner._emit_event("onTickByTick", data, None)

        def onSubscribeAllMarketData(self, exchange_id, error):
            if self._owner:
                self._owner._emit_event("onSubscribeAllMarketData", {"exchange_id": exchange_id}, error)

    _QuoteSpi.__name__ = "_QuoteSpi"
    return _QuoteSpi


class QuoteService(XtpBaseService):

    def __init__(self, cfg: XtpQuoteConfig) -> None:
        super().__init__(cfg)

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        from . import import_quote_api
        self._loop = loop
        vnxtpquote = import_quote_api()
        SpiClass = _make_spi_class(vnxtpquote.QuoteApi)
        api = SpiClass()
        api._owner = self
        api.createQuoteApi(self._cfg.client_id, self._cfg.log_path, self._cfg.log_level)
        api.setHeartBeatInterval(self._cfg.heartbeat_interval)
        self._api = api
        ret = api.login(self._cfg.ip, self._cfg.port, self._cfg.user, self._cfg.password, 1, "")
        if ret != 0:
            raise RuntimeError(f"XTP quote login failed: {api.getApiLastError()}")
        self._started = True
        log.info("XTP quote logged in")

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            if self._api:
                self._api.exit()
        finally:
            self._started = False
            self._force_wake_subscribers()

    # ------------------------------------------------------------------
    # QuoteService
    # ------------------------------------------------------------------
    def subscribe_market_data(self, tickers: list) -> dict:
        if not tickers:
            return {"ret": 0}
        return self._grouped_call(tickers, self._api.subscribeMarketData)

    def unsubscribe_market_data(self, tickers: list) -> dict:
        if not tickers:
            return {"ret": 0}
        return self._grouped_call(tickers, self._api.unsubscribeMarketData)

    def _grouped_call(self, tickers: list, api_fn) -> dict:
        groups: dict[int, list] = {}
        for t in tickers:
            eid = t.get("exchange_id", 2) if isinstance(t, dict) else 2
            groups.setdefault(eid, []).append(t)

        rets = []
        for eid, items in groups.items():
            r = api_fn(items, len(items), eid)
            rets.append(r)
        overall = 0 if all(r == 0 for r in rets) else next(r for r in rets if r != 0)
        return {"ret": overall, "details": [{"exchange_id": eid, "ret": r} for eid, r in zip(groups.keys(), rets)]}
