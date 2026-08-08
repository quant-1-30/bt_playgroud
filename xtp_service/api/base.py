from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def to_serializable(obj: Any) -> Any:
    """XTP C++ struct bytes ---> str
    C++ SPI callback call_soon_threadsafe 
    """
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


class XtpBaseService:
    """
      - start(loop) / stop()
      - SPI self._emit_event(name, data, error) 
    """

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._api = None
        self._started = False
        self._event_subscribers: list[asyncio.Queue] = []

    @property
    def started(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # C++ thread -> asyncio
    # ------------------------------------------------------------------
    def _emit_event(self, event_name: str, data: Any, error: Any = None) -> None:
        """C++ SPI create frame and fan-out to subs
            call_soon_threadsafe submit to asyncio loop
        """
        if self._loop is None:
            return
        frame = {"event": event_name, "data": to_serializable(data), "error": to_serializable(error)}
        for q in list(self._event_subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, frame)

    # ------------------------------------------------------------------
    # subscribe management
    # ------------------------------------------------------------------
    def subscribe_events(self) -> asyncio.Queue:
        """
            register sub with asyncio.Queue and None as sentinel for stop()
        """
        q: asyncio.Queue = asyncio.Queue()
        self._event_subscribers.append(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        if q in self._event_subscribers:
            self._event_subscribers.remove(q)

    # ------------------------------------------------------------------
    # graceful stop: force wake all subs
    # ------------------------------------------------------------------
    def _force_wake_subscribers(self) -> None:
        if self._loop is None:
            return
        for q in list(self._event_subscribers):
            self._loop.call_soon_threadsafe(self._safe_force_put, q, None)
        self._event_subscribers.clear()

    @staticmethod
    def _safe_force_put(q: asyncio.Queue, item: Any) -> None:
        if q.maxsize > 0 and q.full():
            try:
                q._queue.popleft()  
            except IndexError:
                pass
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass  
        