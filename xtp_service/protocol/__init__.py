"""协议层：msgpack 编解码的 Envelope。

帧格式约定（与既有 ZMQ 实现保持兼容）：
- 上行 client → server：`[req_id, payload]`
- 下行 server → client：`[identity, req_id, payload]` * N，末尾追加 `[identity, req_id, b'eof']`
- payload 为 msgpack 编码：
  - 请求 `{method: str, params: dict}`
  - 响应 `{result: ..., error: ...}` 或裸 dict（XTP 回调原样回传）
"""
from __future__ import annotations

from .codec import (
    EOF,
    Request,
    Response,
    decode_payload,
    encode_request,
    encode_response,
    unpack_request,
)

__all__ = [
    "EOF",
    "Request",
    "Response",
    "decode_payload",
    "encode_request",
    "encode_response",
    "unpack_request",
]