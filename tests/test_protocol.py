from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtp_service.protocol.codec import (  # noqa: E402
    EOF,
    RpcError,
    RpcErrorCode,
    decode_payload,
    encode_error,
    encode_request,
    encode_response,
    unpack_request,
)


def _pack(obj) -> bytes:
    import msgpack
    return msgpack.packb(obj, use_bin_type=True)


def test_request_roundtrip():
    payload = encode_request("trader.query_asset", {"ticker": "000001"})
    req = unpack_request(payload)
    assert req.method == "trader.query_asset"
    assert req.params == {"ticker": "000001"}


def test_response_with_result():
    payload = encode_response(result={"total_asset": 100000})
    obj = decode_payload(payload)
    assert obj == {"result": {"total_asset": 100000}}


def test_response_with_error():
    payload = encode_response(error={"code": -1, "message": "fail"})
    obj = decode_payload(payload)
    assert obj == {"error": {"code": -1, "message": "fail"}}


def test_eof_sentinel_constant():
    assert EOF == b"eof"


def test_chinese_string_roundtrip():
    """msgpack use_bin_type=True """
    payload = encode_response(result={"ticker_name": "平安银行"})
    obj = decode_payload(payload)
    assert obj["result"]["ticker_name"] == "平安银行"


def test_client_codec_compatible():
    from client.codec import decode_payload as client_decode, encode_request as client_encode

    req_payload = client_encode("ping", {})
    req = unpack_request(req_payload)
    assert req.method == "ping"

    resp_payload = encode_response(result={"pong": True})
    assert client_decode(resp_payload) == {"result": {"pong": True}}


def test_encode_error_helper():
    """encode_error 应产出仅含 error 字段的帧。"""
    payload = encode_error(RpcErrorCode.METHOD_NOT_FOUND, "no such method")
    obj = decode_payload(payload)
    assert "error" in obj
    assert "result" not in obj
    assert obj["error"]["code"] == -32601
    assert obj["error"]["message"] == "no such method"


def test_unpack_request_missing_method_raises_rpcerror():
    """method 缺失时应抛 RpcError(PARSE_ERROR)，而非 KeyError。"""
    bad_payload = _pack({"params": {}})
    with pytest.raises(RpcError) as exc_info:
        unpack_request(bad_payload)
    assert exc_info.value.code == RpcErrorCode.PARSE_ERROR


def test_unpack_request_garbage_raises_rpcerror():
    with pytest.raises(RpcError) as exc_info:
        unpack_request(b"\x00\x01\x02not-msgpack")
    assert exc_info.value.code == RpcErrorCode.PARSE_ERROR


def test_unpack_request_non_dict_params_coerced():
    payload = _pack({"method": "ping", "params": [1, 2, 3]})
    req = unpack_request(payload)
    assert req.method == "ping"
    assert req.params == {}