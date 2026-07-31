from __future__ import annotations

import msgpack

from dataclasses import dataclass, field
from typing import Any, Optional

EOF = b"eof"


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

def unpack_request(data: bytes) -> Request:
    obj = msgpack.unpackb(data, raw=False)
    return Request(method=obj["method"], params=obj.get("params", {}))

def decode_payload(data: bytes) -> Any:
    return msgpack.unpackb(data, raw=False)