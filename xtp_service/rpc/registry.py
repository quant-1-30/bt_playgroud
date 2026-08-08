from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Union

from ..protocol.codec import Request, RpcError, RpcErrorCode


HandlerResult = Union[AsyncIterator[Any], Awaitable[Any], Any]
Handler = Callable[[dict], HandlerResult]


@dataclass
class HandlerEntry:
    fn: Handler
    streaming: bool = False
    subscription: bool = False


class RpcRegistry:
    """RPC method reflect to handler"""

    def __init__(self) -> None:
        self._handlers: dict[str, HandlerEntry] = {}

    def register(self, method: str, *, streaming: bool = False, subscription: bool = False):
        def _wrap(fn):
            self._handlers[method] = HandlerEntry(fn=fn, streaming=streaming, subscription=subscription)
            return fn
        return _wrap

    def add(self, method: str, fn, *, streaming: bool = False, subscription: bool = False) -> None:
        self._handlers[method] = HandlerEntry(fn=fn, streaming=streaming, subscription=subscription)

    def methods(self) -> list:
        return list(self._handlers.keys())

    def has(self, method: str) -> bool:
        return method in self._handlers

    def is_on_subscribe(self, method: str) -> bool:
        entry = self._handlers.get(method)
        return entry is not None and entry.subscription

    def is_streaming(self, method: str) -> bool:
        entry = self._handlers.get(method)
        return entry is not None and entry.streaming

    async def dispatch(self, request: Request):
        entry = self._handlers.get(request.method)
        if entry is None:
            yield {"error": {"code": RpcErrorCode.METHOD_NOT_FOUND, "message": f"method not found: {request.method}"}}
            return
        try:
            result = entry.fn(request.params)
            if inspect.isasyncgen(result):
                async for item in result:
                    yield item
            elif inspect.isawaitable(result):
                yield await result
            else:
                yield result
        except RpcError as e:
            yield {"error": e.to_dict()}
        except Exception as e:
            yield {"error": {"code": RpcErrorCode.INTERNAL_ERROR, "message": f"{type(e).__name__}: {e}"}}

    async def dispatch_stream(self, request: Request):
        async for item in self.dispatch(request):
            yield item


rpc_registry = RpcRegistry()
