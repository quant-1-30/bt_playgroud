#!/usr/bin/env python3
"""
测试 zmq_client 连接到容器服务
用法: python3 scripts/test_zmq_client.py
"""

import asyncio
import sys
import zmq
import zmq.asyncio
import msgpack
import uuid

# 简化版本，不依赖 bt_studio
class SimpleClient:
    def __init__(self, endpoint: str = "tcp://127.0.0.1:5570"):
        self.endpoint = endpoint
        self._ctx = None
        self._socket = None

    async def connect(self):
        self._ctx = zmq.asyncio.Context()
        self._socket = self._ctx.socket(zmq.DEALER)
        self._socket.setsockopt(zmq.IDENTITY, f'test-{uuid.uuid4()}'.encode())
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(self.endpoint)
        await asyncio.sleep(0.2)

    async def ping(self):
        rid = uuid.uuid4().bytes
        payload = msgpack.packb({'method': 'ping', 'params': {}}, use_bin_type=True)
        await self._socket.send_multipart([rid, payload])

        frames = await self._socket.recv_multipart()
        response = msgpack.unpackb(frames[1], raw=False)
        return response.get('result')

    async def close(self):
        if self._socket:
            self._socket.close(linger=0)
        if self._ctx:
            self._ctx.term()


async def main():
    client = SimpleClient("tcp://127.0.0.1:5570")

    try:
        print("连接到 ZMQ 服务 (tcp://127.0.0.1:5570)...")
        await client.connect()
        print("✓ 已连接")

        print("\n测试 ping...")
        result = await client.ping()
        print(f"✓ ping 响应: {result}")

    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await client.close()
        print("\n连接已关闭")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
