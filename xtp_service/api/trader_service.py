from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Optional

from ..config import XtpTraderConfig

log = logging.getLogger(__name__)


def _to_serializable(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, (bytes, bytearray)):
                try:
                    out[k] = v.decode("utf-8", errors="replace")
                except Exception:
                    out[k] = bytes(v).hex()
            else:
                out[k] = v
        return out
    return obj


def _make_spi_class(trader_base: type) -> type:
    """``trader_base``vnxtptrader.TraderApi SPI
         import vnxtptrader
    """

    class _TraderSpi(trader_base):  # type: ignore[misc, valid-type]
        """XTP TraderApi SPI  ----> TraderService"""

        _owner: Optional["TraderService"] = None

        # connect
        def onDisconnected(self, session_id, reason):
            if self._owner:
                self._owner._on_disconnected(session_id, reason)

        def onError(self, data):
            if self._owner:
                self._owner._on_event("onError", None, data)

        def onQueryAccountTradeMarket(self, trade_location, error, request_id, session_id):
            if self._owner:
                self._owner._on_query(
                    request_id, {"trade_location": trade_location}, error, True, "onQueryAccountTradeMarket"
                )

        # push
        def onOrderEvent(self, data, error, session_id):
            if self._owner:
                self._owner._on_event("onOrderEvent", data, error)

        def onTradeEvent(self, data, session_id):
            if self._owner:
                self._owner._on_event("onTradeEvent", data, None)

        def onCancelOrderError(self, data, error, session_id):
            if self._owner:
                self._owner._on_event("onCancelOrderError", data, error)

        # query
        def onQueryOrder(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryOrder")

        def onQueryOrderEx(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryOrderEx")

        def onQueryTrade(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryTrade")

        def onQueryPosition(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryPosition")

        def onQueryAsset(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryAsset")

        def onQueryFundTransfer(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryFundTransfer")

        def onQueryStructuredFund(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryStructuredFund")

        def onQueryETF(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryETF")

        def onQueryETFBasket(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryETFBasket")

        def onQueryIPOInfoList(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryIPOInfoList")

        def onQueryIPOQuotaInfo(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryIPOQuotaInfo")

        def onQueryCreditFundInfo(self, data, error, reqid, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, True, "onQueryCreditFundInfo")

        def onQueryCreditDebtInfo(self, data, error, reqid, last, session_id):
            if self._owner:
                self._owner._on_query(reqid, data, error, last, "onQueryCreditDebtInfo")

    _TraderSpi.__name__ = "_TraderSpi"
    return _TraderSpi


class TraderService:
    
    def __init__(self, cfg: XtpTraderConfig) -> None:
        self._cfg = cfg
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._api = None
        self._session_id: int = 0
        self._reqid: int = 0
        self._query_queues: dict[int, asyncio.Queue] = {}
        self._event_subscribers: list[asyncio.Queue] = []
        self._started = False

    # ------------------------------------------------------------------
    # lifetime
    # ------------------------------------------------------------------
    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        from . import import_trader_api

        self._loop = loop
        vnxtptrader = import_trader_api()
        SpiClass = _make_spi_class(vnxtptrader.TraderApi)

        api = SpiClass()
        api._owner = self  # type: ignore[attr-defined]
        api.createTraderApi(self._cfg.client_id, self._cfg.log_path, self._cfg.log_level)
        api.subscribePublicTopic(2)
        api.setSoftwareKey(self._cfg.software_key)
        api.setSoftwareVersion(self._cfg.software_version)
        api.setHeartBeatInterval(self._cfg.heartbeat_interval)
        self._api = api

        session_id = api.login(
            self._cfg.ip, self._cfg.port, self._cfg.user, self._cfg.password, 1, ""
        )
        if session_id == 0:
            err = api.getApiLastError()
            raise RuntimeError(f"XTP trader login failed: {err}")
        self._session_id = session_id
        self._started = True
        log.info("XTP trader logged in, session_id=%s", session_id)

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            if self._api is not None and self._session_id:
                self._api.logout(self._session_id)
                self._api.exit()
        finally:
            self._started = False
            for q in list(self._query_queues.values()):
                _safe_put(q, {"error": {"code": -32001, "message": "service stopped"}})
            for q in self._event_subscribers:
                _safe_put(q, None)

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def started(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # SPI Bridge C++ thread
    # ------------------------------------------------------------------
    def _on_query(self, reqid: int, data: Any, error: Any, last: bool, event_name: str) -> None:
        if self._loop is None:
            return
        q = self._query_queues.get(reqid)
        if q is None:
            return
        frame = {
            "event": event_name,
            "data": _to_serializable(data),
            "error": _to_serializable(error),
            "last": bool(last),
        }
        self._loop.call_soon_threadsafe(q.put_nowait, frame)

    def _on_event(self, event_name: str, data: Any, error: Any = None) -> None:
        if self._loop is None:
            return
        frame = {"event": event_name, "data": _to_serializable(data), "error": _to_serializable(error)}
        for q in list(self._event_subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, frame)

    def _on_disconnected(self, session_id: int, reason: int) -> None:
        log.warning("XTP trader disconnected: session=%s reason=%s", session_id, reason)
        self._on_event("onDisconnected", {"session_id": session_id, "reason": reason})

    # ------------------------------------------------------------------
    # RPC
    # ------------------------------------------------------------------
    def _next_reqid(self) -> int:
        self._reqid += 1
        return self._reqid

    def _register_query(self, reqid: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._query_queues[reqid] = q
        return q

    def _unregister_query(self, reqid: int) -> None:
        self._query_queues.pop(reqid, None)

    async def _do_query(self, method_name: str, api_call) -> AsyncIterator[dict]:
        """request → queue → yield frame"""
        reqid = self._next_reqid()
        q = self._register_query(reqid)
        try:
            ret = api_call(reqid)
            if ret != 0:
                yield {"error": {"code": ret, "message": f"{method_name} send failed, ret={ret}"}}
                return
            while True:
                frame = await q.get()
                if frame is None:
                    return
                yield frame
                if frame.get("last"):
                    return
        finally:
            self._unregister_query(reqid)

    async def query_asset(self) -> AsyncIterator[dict]:
        async for frame in self._do_query(
            "queryAsset", lambda rid: self._api.queryAsset(self._session_id, rid)
        ):
            yield frame

    async def query_position(self, ticker: str = "") -> AsyncIterator[dict]:
        async for frame in self._do_query(
            "queryPosition", lambda rid: self._api.queryPosition(ticker, self._session_id, rid)
        ):
            yield frame

    async def query_order(self, req: dict) -> AsyncIterator[dict]:
        async for frame in self._do_query(
            "queryOrders", lambda rid: self._api.queryOrders(req, self._session_id, rid)
        ):
            yield frame

    async def query_trade(self, req: dict) -> AsyncIterator[dict]:
        async for frame in self._do_query(
            "queryTrades", lambda rid: self._api.queryTrades(req, self._session_id, rid)
        ):
            yield frame

    async def query_account_trade_market(self) -> AsyncIterator[dict]:
        async for frame in self._do_query(
            "queryAccountTradeMarket",
            lambda rid: self._api.queryAccountTradeMarket(self._session_id, rid),
        ):
            yield frame

    def insert_order(self, req: dict) -> dict:
        """order_xtp_id"""
        order_xtp_id = self._api.insertOrder(req, self._session_id)
        return {"order_xtp_id": order_xtp_id}

    def cancel_order(self, order_xtp_id: int) -> dict:
        ret = self._api.cancelOrder(order_xtp_id, self._session_id)
        return {"ret": ret}

    def subscribe_events(self) -> asyncio.Queue:
        """event queue"""
        q: asyncio.Queue = asyncio.Queue()
        self._event_subscribers.append(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        if q in self._event_subscribers:
            self._event_subscribers.remove(q)


def _safe_put(q: asyncio.Queue, item: Any) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass
