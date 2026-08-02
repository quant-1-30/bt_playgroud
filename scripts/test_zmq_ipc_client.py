#!/usr/bin/env python3
"""
主机端 ZMQ IPC 客户端测试
使用 UNIX Socket 与容器内 XTP 服务通信
"""

import asyncio
import sys
import zmq
import zmq.asyncio
import msgpack
import uuid
from pathlib import Path


class IPCClient:
    """ZMQ IPC 客户端"""

    def __init__(self, ipc_path: str = None):
        # 默认使用项目目录下的 .ipc/xtp.ipc
        if ipc_path is None:
            project_dir = Path(__file__).resolve().parent.parent
            ipc_path = str(project_dir / ".ipc" / "xtp.ipc")
        self.endpoint = f"ipc://{ipc_path}"
        self._ctx = None
        self._socket = None

    async def connect(self):
        """连接到 IPC socket"""
        self._ctx = zmq.asyncio.Context()
        self._socket = self._ctx.socket(zmq.DEALER)
        self._socket.setsockopt(zmq.IDENTITY, f'host-{uuid.uuid4()}'.encode())
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(self.endpoint)
        await asyncio.sleep(0.1)

    async def call(self, method: str, params: dict = None) -> dict:
        """调用 RPC 方法"""
        if params is None:
            params = {}

        rid = uuid.uuid4().bytes
        payload = msgpack.packb({'method': method, 'params': params}, use_bin_type=True)

        await self._socket.send_multipart([rid, payload])

        # 接收响应流
        results = []
        while True:
            frames = await self._socket.recv_multipart()
            response = msgpack.unpackb(frames[1], raw=False)

            # 检查是否为 EOF
            if frames == [rid, b'\x00\x00\x00\xff']:
                break

            if 'result' in response:
                results.append(response['result'])
            elif 'error' in response:
                return {'error': response['error']}

        return results[0] if len(results) == 1 else results

    async def ping(self) -> dict:
        """测试连接"""
        return await self.call('ping', {})

    async def close(self):
        """关闭连接"""
        if self._socket:
            self._socket.close(linger=0)
        if self._ctx:
            self._ctx.term()


async def main():
    """主测试函数"""
    project_dir = Path(__file__).resolve().parent.parent
    ipc_path = str(project_dir / ".ipc" / "xtp.ipc")

    client = IPCClient(ipc_path)

    try:
        print("========================================")
        print("ZMQ IPC 客户端测试")
        print("========================================")
        print(f"Socket 路径: {ipc_path}")
        print()

        # 检查 socket 文件是否存在
        if not Path(ipc_path).exists():
            print(f"❌ Socket 文件不存在: {ipc_path}")
            print("请先启动容器服务:")
            print("  ./scripts/container_run_ipc.sh")
            return 1

        print("连接到 ZMQ IPC 服务...")
        await client.connect()
        print("✓ 已连接")

        print()
        print("测试 ping...")
        result = await client.ping()
        print(f"✓ ping 响应: {result}")

        print()
        print("========================================")
        print("✓ 所有测试通过")
        print("========================================")

        return 0

    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
