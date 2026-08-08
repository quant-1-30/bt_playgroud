from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtp_service.api.trader_service import TraderService  # noqa: E402
from xtp_service.config import XtpTraderConfig  # noqa: E402
from xtp_service.protocol.codec import RpcErrorCode  # noqa: E402


class FakeTraderApi:
    def __init__(self):
        self.calls = []

    def queryAsset(self, session_id, reqid):
        self.calls.append(("queryAsset", session_id, reqid))
        return 0


async def test_do_query_timeout_when_no_callback():
    cfg = XtpTraderConfig()
    svc = TraderService(cfg, query_timeout=0.3)
    svc._loop = asyncio.get_running_loop()
    svc._api = FakeTraderApi()
    svc._session_id = 1
    svc._started = True

    frames = []
    async for frame in svc.query_asset():
        frames.append(frame)

    assert len(frames) == 1
    assert "error" in frames[0]
    assert frames[0]["error"]["code"] == RpcErrorCode.QUERY_TIMEOUT
    assert "timeout" in frames[0]["error"]["message"].lower()
    assert len(svc._query_queues) == 0


async def test_do_query_send_failed_returns_error_immediately():
    cfg = XtpTraderConfig()
    svc = TraderService(cfg, query_timeout=10.0)
    svc._loop = asyncio.get_running_loop()
    svc._api = type("F", (), {"queryAsset": lambda self, sid, rid: -1})()
    svc._session_id = 1
    svc._started = True

    frames = []
    async for frame in svc.query_asset():
        frames.append(frame)

    assert len(frames) == 1
    assert frames[0]["error"]["code"] == -1
    assert len(svc._query_queues) == 0
