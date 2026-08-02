#!/bin/bash
# 在容器内运行 ZMQ 客户端测试
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================="
echo "在容器内运行 ZMQ 客户端"
echo "========================================="

container exec xtp-service-auto python3 -c "
import asyncio
import zmq.asyncio
import msgpack
import uuid

async def test():
    ctx = zmq.asyncio.Context()
    socket = ctx.socket(zmq.DEALER)
    socket.setsockopt(zmq.IDENTITY, f'test-{uuid.uuid4()}'.encode())
    socket.setsockopt(zmq.LINGER, 0)
    socket.connect('tcp://127.0.0.1:5570')

    rid = uuid.uuid4().bytes
    payload = msgpack.packb({'method': 'ping', 'params': {}}, use_bin_type=True)

    print('Sending ping...')
    await socket.send_multipart([rid, payload])

    frames = await socket.recv_multipart()
    print(f'Got {len(frames)} frames')

    response = msgpack.unpackb(frames[1], raw=False)
    print(f'Response: {response}')

    socket.close()
    ctx.term()

asyncio.run(test())
"
