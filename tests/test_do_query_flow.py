from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtp_service.api.trader_service import TraderService  # noqa: E402
from xtp_service.config import XtpTraderConfig  # noqa: E402


class FakeTraderApi:
    """patch XTP C++ API queryAsset and processTask onQueryAsset callback"""

    def __init__(self):
        self._owner = None

    def queryAsset(self, session_id, reqid):
        """
          1. queryAsset(session_id, reqid) 0
          2. C++ processTask 线程收到网络响应后回调 onQueryAsset
        """
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, self._simulate_callback, reqid)
        return 0

    def _simulate_callback(self, reqid):
        fake_data = {"total_asset": 100000, "buying_power": 50000}
        fake_error = {"error_id": 0, "error_msg": ""}
        self._owner.onQueryAsset(fake_data, fake_error, reqid, True, 1)


async def test_do_query_success_with_callback():
    cfg = XtpTraderConfig()
    svc = TraderService(cfg, query_timeout=5.0)
    svc._loop = asyncio.get_running_loop()
    fake_api = FakeTraderApi()
    svc._api = fake_api
    svc._session_id = 1
    svc._started = True

    # Create SPI-like object that forwards onQueryAsset to svc._on_query
    class FakeSpi:
        def onQueryAsset(self, data, error, reqid, last, session_id):
            svc._on_query(reqid, data, error, last, "onQueryAsset")
    fake_api._owner = FakeSpi()

    frames = []
    async for frame in svc.query_asset():
        frames.append(frame)

    assert len(frames) == 1
    assert frames[0]["event"] == "onQueryAsset"
    assert frames[0]["data"]["total_asset"] == 100000
    assert frames[0]["last"] is True
    assert len(svc._query_queues) == 0
