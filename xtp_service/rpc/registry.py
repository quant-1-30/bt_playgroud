"""RPC 注册表：把 method 名映射到 handler。"""
from __future__ import annotations

import inspect
from typing import Any, AsyncIterator, Awaitable, Callable, Union

from ..protocol.codec import Request


HandlerResult = Union[AsyncIterator[Any], Awaitable[Any], Any]
Handler = Callable[[dict], HandlerResult]


class RpcRegistry:
    """method -> handler 注册表。"""

    def __init__(self) -> None:
        self._handlers: dict = {}

    def register(self, method: str):
        """装饰器：注册 handler。"""
        def _wrap(fn):
            self._handlers[method] = fn
            return fn
        return _wrap

    def add(self, method: str, fn) -> None:
        """直接注册 handler。"""
        self._handlers[method] = fn

    def methods(self) -> list:
        return list(self._handlers.keys())

    def has(self, method: str) -> bool:
        return method in self._handlers

    async def dispatch(self, request: Request):
        """分发请求，异步产出响应帧。"""
        handler = self._handlers.get(request.method)
        if handler is None:
            yield {"error": {"code": -32601, "message": f"method not found: {request.method}"}}
            return

        try:
            result = handler(request.params)
            if inspect.isasyncgen(result):
                async for item in result:
                    yield item
            elif inspect.isawaitable(result):
                yield await result
            else:
                yield result
        except Exception as e:
            yield {"error": {"code": -32000, "message": f"{type(e).__name__}: {e}"}}


rpc_registry = RpcRegistry()