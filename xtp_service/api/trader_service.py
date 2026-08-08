from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from ..config import XtpTraderConfig
from ..protocol.codec import RpcErrorCode
from .base import XtpBaseService, to_serializable

log = logging.getLogger(__name__)


@dataclass
class SubscriberHandle:
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    unsubscribe: Optional[Callable[[], None]] = None


def _make_spi_class(trader_base: type) -> type:
    """XTP TraderApi SPI and delegate C++ callback"""

    class _TraderSpi(trader_base):  # type: ignore[misc, valid-type]
        _owner: Optional["TraderService"] = None

        def onDisconnected(self, session_id, reason):
            if self._owner:
                log.warning("XTP trader disconnected: session=%s reason=%s", session_id, reason)
                self._owner._emit_event("onDisconnected", {"session_id": session_id, "reason": reason})

        def onError(self, data):
            if self._owner:
                self._owner._emit_event("onError", None, data)

        # --- push Event ---
        def onOrderEvent(self, data, error, session_id):
            if self._owner:
                self._owner._emit_event("onOrderEvent", data, error)

        def onTradeEvent(self, data, session_id):
            if self._owner:
                self._owner._emit_event("onTradeEvent", data, None)

        def onCancelOrderError(self, data, error, session_id):
            if self._owner:
                self._owner._emit_event("onCancelOrderError", data, error)

        # --- query callback ---
        def onQueryAccountTradeMarket(self, trade_location, error, request_id, session_id):
            if self._owner:
                self._owner._on_query(request_id, {"trade_location": trade_location}, error, True, "onQueryAccountTradeMarket")
        
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


class TraderService(XtpBaseService):
    """_query_queues + _do_query。
    """

    def __init__(self, cfg: XtpTraderConfig, query_timeout: float = 15.0) -> None:
        super().__init__(cfg)
        self._query_timeout: float = query_timeout
        self._session_id: int = 0
        self._reqid: int = 0
        self._query_queues: dict[int, asyncio.Queue] = {}

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        from . import import_trader_api
        self._loop = loop
        vnxtptrader = import_trader_api()
        SpiClass = _make_spi_class(vnxtptrader.TraderApi)
        api = SpiClass()
        api._owner = self
        api.createTraderApi(self._cfg.client_id, self._cfg.log_path, self._cfg.log_level)
        api.subscribePublicTopic(2)
        api.setSoftwareKey(self._cfg.software_key)
        api.setSoftwareVersion(self._cfg.software_version)
        api.setHeartBeatInterval(self._cfg.heartbeat_interval)
        self._api = api
        session_id = api.login(self._cfg.ip, self._cfg.port, self._cfg.user, self._cfg.password, 1, "")
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
            self._force_wake_all()

    def _force_wake_all(self) -> None:
        if self._loop is None:
            return
        for q in list(self._query_queues.values()):
            self._loop.call_soon_threadsafe(
                self._safe_force_put, q,
                {"error": {"code": RpcErrorCode.SERVICE_STOPPED, "message": "service stopped"}}
            )
        self._query_queues.clear()
        self._force_wake_subscribers()

    @property
    def session_id(self) -> int:
        return self._session_id

    # ------------------------------------------------------------------
    # Boost Python virtual function callback
    # ------------------------------------------------------------------
    def _on_query(self, reqid: int, data: Any, error: Any, last: bool, event_name: str) -> None:
        """reqid dispatcher to query queue"""
        if self._loop is None:
            return
        q = self._query_queues.get(reqid)
        if q is None:
            return
        frame = {"event": event_name, "data": to_serializable(data), "error": to_serializable(error), "last": bool(last)}
        self._loop.call_soon_threadsafe(q.put_nowait, frame)

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
        reqid = self._next_reqid()
        q = self._register_query(reqid)
        try:
            ret = api_call(reqid)
            if ret != 0:
                yield {"error": {"code": ret, "message": f"{method_name} send failed, ret={ret}"}}
                return
            while True:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=self._query_timeout)
                except asyncio.TimeoutError:
                    yield {"error": {"code": RpcErrorCode.QUERY_TIMEOUT, "message": f"XTP query timeout: {method_name} (>{self._query_timeout}s)"}}
                    return
                if frame is None:
                    return
                yield frame
                if isinstance(frame, dict) and frame.get("last"):
                    return
        finally:
            self._unregister_query(reqid)

    # ------------------------------------------------------------------
    # RPC
    # ------------------------------------------------------------------
    async def query_asset(self) -> AsyncIterator[dict]:
        async for frame in self._do_query("queryAsset", lambda rid: self._api.queryAsset(self._session_id, rid)):
            yield frame

    async def query_position(self, ticker: str = "") -> AsyncIterator[dict]:
        async for frame in self._do_query("queryPosition", lambda rid: self._api.queryPosition(ticker, self._session_id, rid)):
            yield frame

    async def query_order(self, req: dict) -> AsyncIterator[dict]:
        async for frame in self._do_query("queryOrders", lambda rid: self._api.queryOrders(req, self._session_id, rid)):
            yield frame

    async def query_trade(self, req: dict) -> AsyncIterator[dict]:
        async for frame in self._do_query("queryTrades", lambda rid: self._api.queryTrades(req, self._session_id, rid)):
            yield frame

    async def query_account_trade_market(self) -> AsyncIterator[dict]:
        async for frame in self._do_query("queryAccountTradeMarket", lambda rid: self._api.queryAccountTradeMarket(self._session_id, rid)):
            yield frame

    def insert_order(self, req: dict) -> dict:
        order_xtp_id = self._api.insertOrder(req, self._session_id)
        return {"order_xtp_id": order_xtp_id}

    def cancel_order(self, order_xtp_id: int) -> dict:
        ret = self._api.cancelOrder(order_xtp_id, self._session_id)
        return {"ret": ret}
