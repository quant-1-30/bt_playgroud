from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xtp_service.transport.pubsub import BroadcastHub, Subscriber, Broadcaster  # noqa: E402


async def test_broadcast_hub_subscribe_publish():
    hub = BroadcastHub(maxsize=10)
    sub = hub.subscribe(b"client1", b"req1")
    await hub.publish({"event": "test", "data": 1})
    frame = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert frame == {"event": "test", "data": 1}


async def test_broadcast_hub_multi_subscriber():
    hub = BroadcastHub(maxsize=10)
    sub1 = hub.subscribe(b"c1", b"r1")
    sub2 = hub.subscribe(b"c2", b"r2")
    await hub.publish({"event": "ping"})
    f1 = await asyncio.wait_for(sub1.queue.get(), timeout=1.0)
    f2 = await asyncio.wait_for(sub2.queue.get(), timeout=1.0)
    assert f1 == {"event": "ping"}
    assert f2 == {"event": "ping"}


async def test_broadcast_hub_drop_oldest_on_full():
    hub = BroadcastHub(maxsize=2, drop_policy="drop_oldest")
    sub = hub.subscribe(b"c1", b"r1")
    # Fill queue to capacity + 1 overflow
    await hub.publish({"i": 1})
    await hub.publish({"i": 2})
    dropped = await hub.publish({"i": 3})
    assert dropped >= 1
    # Should have the latest 2 frames
    f1 = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    f2 = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert f1["i"] == 2
    assert f2["i"] == 3


async def test_broadcast_hub_unsubscribe_stops_delivery():
    hub = BroadcastHub(maxsize=10)
    sub = hub.subscribe(b"c1", b"r1")
    hub.unsubscribe(sub)
    assert not sub.alive
    await hub.publish({"event": "should_not_deliver"})
    assert sub.queue.empty()
    assert hub.subscriber_count == 0


async def test_broadcaster_run_sends_frames_with_identity():
    hub = BroadcastHub(maxsize=10)
    sub = hub.subscribe(b"client-identity", b"req-id")

    source_q: asyncio.Queue = asyncio.Queue()
    sent_frames = []

    class FakeSocket:
        async def send_multipart(self, frames):
            sent_frames.append(frames)

    bc = Broadcaster(source_q, hub, FakeSocket(), "test")
    task = asyncio.create_task(bc.run())
    await asyncio.sleep(0.05)

    await source_q.put({"event": "onOrderEvent", "data": {"price": 10.5}})
    await asyncio.sleep(0.1)

    await bc.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(sent_frames) == 1
    frames = sent_frames[0]
    assert frames[0] == b"client-identity"
    assert frames[1] == b"req-id"
    assert len(frames[2]) > 0  # encoded body
