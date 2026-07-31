"""XTP 原生 API 封装层（仅在 ARM Linux 容器内可用）。

对 vnxtptrader.so / vnxtpquote.so 做延迟导入，失败时给出清晰报错。
SPI 回调（C++ 线程）通过 asyncio.Queue + loop.call_soon_threadsafe 桥接到事件循环。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def load_native_libs(native_dir: Path) -> None:
    """把原生库目录加入动态搜索路径。

    native_dir 形如 .../xtp_service/native
    其下含 vnxtptrader.so / vnxtpquote.so（Boost.Python 封装）
    以及 libxtptraderapi.so / libxtpquoteapi.so（C++ 实现）。
    """
    native_dir = Path(native_dir)
    p = str(native_dir)
    if p not in sys.path:
        sys.path.insert(0, p)
    if sys.platform.startswith("linux"):
        ld = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = p + (":" + ld if ld else "")


def import_trader_api():
    """延迟导入 vnxtptrader；失败给清晰提示。"""
    try:
        import vnxtptrader  # noqa: F401
        return vnxtptrader
    except Exception as e:
        raise RuntimeError(
            "无法加载 vnxtptrader.so：请确认在 ARM Linux 容器内运行，"
            f"且已通过 load_native_libs() 设置库路径。原始错误：{e}"
        ) from e


def import_quote_api():
    """延迟导入 vnxtpquote；失败给清晰提示。"""
    try:
        import vnxtpquote  # noqa: F401
        return vnxtpquote
    except Exception as e:
        raise RuntimeError(
            "无法加载 vnxtpquote.so：请确认在 ARM Linux 容器内运行，"
            f"且已通过 load_native_libs() 设置库路径。原始错误：{e}"
        ) from e
