from __future__ import annotations

import msgpack

from dataclasses import dataclass, field
from typing import Any, Optional

EOF = b"eof"


"""
frame format: compatiable with zmq
- client → server `[req_id, payload]`
- server → client `[identity, req_id, payload]` * N + `[identity, req_id, b'eof']`
- payload msgpack encode:
  - `{method: str, params: dict}`
  - `{result: ..., error: ...}`
"""

class RpcErrorCode:
    """JSON-RPC 2.0 """
    PARSE_ERROR = -32700
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32000
    SERVICE_STOPPED = -32001
    RATE_LIMITED = -32004
    QUERY_TIMEOUT = -32010
    SUBSCRIBER_GONE = -32011


class RpcError(Exception):
    """RPC Exception and raise handler / codec and Error Frame"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass
class Request:
    method: str
    params: dict = field(default_factory=dict)

@dataclass
class Response:
    result: Any = None
    error: Optional[dict] = None

def encode_request(method: str, params: dict) -> bytes:
    return msgpack.packb({"method": method, "params": params}, use_bin_type=True)

def encode_response(result: Any = None, error: Optional[dict] = None) -> bytes:
    payload: dict = {}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return msgpack.packb(payload, use_bin_type=True)

def encode_error(code: int, message: str) -> bytes:
    return encode_response(error={"code": code, "message": message})

def unpack_request(data: bytes) -> Request:
    try:
        obj = msgpack.unpackb(data, raw=False)
    except Exception as e:
        raise RpcError(RpcErrorCode.PARSE_ERROR, f"msgpack decode failed: {e}") from e
    if not isinstance(obj, dict) or "method" not in obj:
        raise RpcError(RpcErrorCode.PARSE_ERROR, "missing 'method' field in request")
    params = obj.get("params", {})
    if not isinstance(params, dict):
        params = {}
    return Request(method=obj["method"], params=params)

def decode_payload(data: bytes) -> Any:
    return msgpack.unpackb(data, raw=False)
