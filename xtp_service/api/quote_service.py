from __future__ import annotations

import asyncio, logging

from typing import Any, Optional
from ..config import XtpQuoteConfig

log = logging.getLogger(__name__)


def _to(obj):
    if obj is None: return None
    if isinstance(obj, dict):
        return {k: (v.decode("utf-8","replace") if isinstance(v,(bytes,bytearray)) else v) for k,v in obj.items()}
    return obj


def _make(base):
    class S(base):
        _owner = None
        def onDisconnected(self, r):
            if self._owner: self._owner._ev("onDisconnected", {"reason": r})
        def onError(self, d):
            if self._owner: self._owner._ev("onError", None, d)
        def onSubMarketData(self, d, e, l):
            if self._owner: self._owner._ev("onSubMarketData", d, e)
        def onDepthMarketData(self, d, *a):
            if self._owner: self._owner._ev("onDepthMarketData", d, None)
        def onSubOrderBook(self, d, e, l):
            if self._owner: self._owner._ev("onSubOrderBook", d, e)
        def onOrderBook(self, d):
            if self._owner: self._owner._ev("onOrderBook", d, None)
        def onSubTickByTick(self, d, e, l):
            if self._owner: self._owner._ev("onSubTickByTick", d, e)
        def onTickByTick(self, d):
            if self._owner: self._owner._ev("onTickByTick", d, None)
        def onSubscribeAllMarketData(self, eid, e):
            if self._owner: self._owner._ev("onSubscribeAllMarketData", {"exchange_id": eid}, e)
    S.__name__ = "_QuoteSpi"
    return S


class QuoteService:
    """XTP QuoteApi SPI bridge to asyncio."""

    def __init__(self, cfg):
        self._cfg = cfg; self._loop = None; self._api = None
        self._started = False; self._subs = []

    async def start(self, loop):
        from . import import_quote_api
        self._loop = loop
        q = import_quote_api()
        S = _make(q.QuoteApi)
        api = S(); api._owner = self
        api.createQuoteApi(self._cfg.client_id, self._cfg.log_path, self._cfg.log_level)
        api.setHeartBeatInterval(self._cfg.heartbeat_interval)
        self._api = api
        ret = api.login(self._cfg.ip, self._cfg.port, self._cfg.user, self._cfg.password, 1, "")
        if ret != 0:
            raise RuntimeError(f"XTP quote login failed: {api.getApiLastError()}")
        self._started = True
        log.info("XTP quote logged in")

    async def stop(self):
        if not self._started: return
        try:
            if self._api: self._api.exit()
        finally:
            self._started = False
            for q in self._subs:
                try: q.put_nowait(None)
                except: pass

    @property
    def started(self): return self._started

    def _ev(self, name, data, error=None):
        if self._loop is None: return
        f = {"event": name, "data": _to(data), "error": _to(error)}
        for q in list(self._subs):
            self._loop.call_soon_threadsafe(q.put_nowait, f)

    def subscribe_market_data(self, tl):
        eid = tl[0].get("exchange_id", 2) if tl else 2
        return {"ret": self._api.subscribeMarketData(tl, len(tl), eid)}

    def unsubscribe_market_data(self, tl):
        eid = tl[0].get("exchange_id", 2) if tl else 2
        return {"ret": self._api.unsubscribeMarketData(tl, len(tl), eid)}

    def subscribe_events(self):
        q = asyncio.Queue(); self._subs.append(q); return q

    def unsubscribe_events(self, q):
        if q in self._subs: self._subs.remove(q)
