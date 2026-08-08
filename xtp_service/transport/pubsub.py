from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class Subscriber:
    identity: bytes          # client ROUTER identity
    req_id: bytes            # req_id for route
    queue: asyncio.Queue     # maxsize=HWM
    alive: bool = True


class BroadcastHub:
    """
    a. event_source hub and Subscriber
    b. put to hub and fan-out Subscriber.queue
    Queue.Full
      - "drop_oldest"
      - "block"
    """

    def __init__(self, maxsize: int = 4096, drop_policy: str = "drop_oldest") -> None:
        self.maxsize = maxsize
        self.drop_policy = drop_policy
        self._subscribers: list[Subscriber] = []
        self._dropped_count: int = 0

    def subscribe(self, identity: bytes, req_id: bytes) -> Subscriber:
        sub = Subscriber(
            identity=identity,
            req_id=req_id,
            queue=asyncio.Queue(maxsize=self.maxsize),
        )
        self._subscribers.append(sub)
        log.info("Subscriber added: identity=%s, total=%d", identity[:16], len(self._subscribers))
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        sub.alive = False
        if sub in self._subscribers:
            self._subscribers.remove(sub)
        log.info("Subscriber removed: identity=%s, total=%d", sub.identity[:16], len(self._subscribers))

    def unsubscribe_all(self) -> None:
        for sub in list(self._subscribers):
            sub.alive = False
        self._subscribers.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    def _put_drop_oldest(self, q: asyncio.Queue, frame: Any) -> bool:
        """
        nowait is sync method 
        Returns:
            bool: 
        """
        dropped = False
        while True:
            try:
                q.put_nowait(frame)
                return dropped
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    dropped = True  
                except asyncio.QueueEmpty:
                    pass  

    async def publish(self, frame: dict) -> int:
        dropped = 0
        for sub in list(self._subscribers):
            if not sub.alive:
                continue

            if self.drop_policy == "drop_oldest":
                if self._put_drop_oldest(sub.queue, frame):
                    dropped += 1
            else:
                await sub.queue.put(frame)

        self._dropped_count += dropped
        return dropped

    def publish_nowait(self, frame: dict) -> int:
        dropped = 0
        for sub in list(self._subscribers):
            if not sub.alive:
                continue

            try:
                sub.queue.put_nowait(frame)
            except asyncio.QueueFull:
                if self.drop_policy == "drop_oldest":
                    if self._put_drop_oldest(sub.queue, frame):
                        dropped += 1
                else:
                    dropped += 1  

        self._dropped_count += dropped
        return dropped


class Broadcaster:
    """
      frame[identity, req_id, body] 
      broadcaster = Broadcaster(trader_event_queue, hub, router_socket, "trader")
      task = asyncio.create_task(broadcaster.run())
      await broadcaster.stop()
    """

    def __init__(
        self,
        source_queue: asyncio.Queue,
        hub: BroadcastHub,
        send_socket,
        log_name: str = "broadcaster",
    ) -> None:
        self.source_queue = source_queue
        self.hub = hub
        self.send_socket = send_socket
        self.log_name = log_name
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def run(self) -> None:
        from xtp_service.protocol import encode_response
        self._running = True
        log.info("%s broadcaster started", self.log_name)

        while self._running:
            try:
                frame = await self.source_queue.get()
                if frame is None:
                    break
                # fan-out to hub queues
                await self.hub.publish(frame)
                # send to each subscriber via socket
                for sub in list(self.hub._subscribers):
                    if not sub.alive:
                        continue
                    body = encode_response(result=frame)
                    try:
                        await self.send_socket.send_multipart([sub.identity, sub.req_id, body])
                    except Exception as e:
                        log.warning("%s broadcaster: send failed to %s, unsubscribing: %s",
                                    self.log_name, sub.identity[:16], e)
                        self.hub.unsubscribe(sub)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("%s broadcaster error: %s", self.log_name, e)

        log.info("%s broadcaster stopped", self.log_name)

    def start(self) -> asyncio.Task:
        self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._running = False
        try:
            self.source_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.hub.unsubscribe_all()
