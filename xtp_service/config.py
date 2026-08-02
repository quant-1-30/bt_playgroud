from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NATIVE_DIR = Path(__file__).resolve().parent / "native"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"


@dataclass
class XtpTraderConfig:
    ip: str = "122.112.139.0"
    port: int = 6202
    user: str = ""
    password: str = ""
    software_key: str = ""
    software_version: str = "xtp-service"
    client_id: int = 2
    log_path: str = "./log"
    log_level: int = 4
    heartbeat_interval: int = 15


@dataclass
class XtpQuoteConfig:
    ip: str = "119.3.103.38"
    port: int = 6002
    user: str = ""
    password: str = ""
    client_id: int = 1
    log_path: str = "./log"
    log_level: int = 4
    heartbeat_interval: int = 15


@dataclass
class ZmqServerConfig:
    host: str = "0.0.0.0"
    port: int = 5570
    backend_port: int = 5571
    max_workers: int = 8
    rate_limit_per_minute: int = 0
    ipc_path: str = ""  # IPC socket 路径，非空时使用 IPC 而非 TCP


@dataclass
class Settings:
    native_dir: Path = DEFAULT_NATIVE_DIR
    trader: XtpTraderConfig = field(default_factory=XtpTraderConfig)
    quote: XtpQuoteConfig = field(default_factory=XtpQuoteConfig)
    zmq: ZmqServerConfig = field(default_factory=ZmqServerConfig)


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_settings(config_path: Optional[Path] = None) -> Settings:
    cfg_path = Path(config_path or os.getenv("XTP_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    raw = _load_toml(cfg_path)

    trader_raw = raw.get("trader", {})
    quote_raw = raw.get("quote", {})
    zmq_raw = raw.get("zmq", {})

    settings = Settings(
        native_dir=Path(os.getenv("XTP_NATIVE_DIR", raw.get("native_dir", str(DEFAULT_NATIVE_DIR)))),
        trader=XtpTraderConfig(
            ip=os.getenv("XTP_TRADER_IP", trader_raw.get("ip", XtpTraderConfig.ip)),
            port=int(os.getenv("XTP_TRADER_PORT", trader_raw.get("port", XtpTraderConfig.port))),
            user=os.getenv("XTP_TRADER_USER", trader_raw.get("user", "")),
            password=os.getenv("XTP_TRADER_PASSWORD", trader_raw.get("password", "")),
            software_key=os.getenv("XTP_TRADER_KEY", trader_raw.get("software_key", "")),
            software_version=os.getenv("XTP_SW_VERSION", trader_raw.get("software_version", "xtp-service")),
            client_id=int(os.getenv("XTP_TRADER_CLIENT_ID", trader_raw.get("client_id", 2))),
            log_path=os.getenv("XTP_TRADER_LOG", trader_raw.get("log_path", "./log")),
            log_level=int(os.getenv("XTP_TRADER_LOG_LEVEL", trader_raw.get("log_level", 4))),
            heartbeat_interval=int(os.getenv("XTP_TRADER_HB", trader_raw.get("heartbeat_interval", 15))),
        ),
        quote=XtpQuoteConfig(
            ip=os.getenv("XTP_QUOTE_IP", quote_raw.get("ip", XtpQuoteConfig.ip)),
            port=int(os.getenv("XTP_QUOTE_PORT", quote_raw.get("port", XtpQuoteConfig.port))),
            user=os.getenv("XTP_QUOTE_USER", quote_raw.get("user", "")),
            password=os.getenv("XTP_QUOTE_PASSWORD", quote_raw.get("password", "")),
            client_id=int(os.getenv("XTP_QUOTE_CLIENT_ID", quote_raw.get("client_id", 1))),
            log_path=os.getenv("XTP_QUOTE_LOG", quote_raw.get("log_path", "./log")),
            log_level=int(os.getenv("XTP_QUOTE_LOG_LEVEL", quote_raw.get("log_level", 4))),
            heartbeat_interval=int(os.getenv("XTP_QUOTE_HB", quote_raw.get("heartbeat_interval", 15))),
        ),
        zmq=ZmqServerConfig(
            host=os.getenv("ZMQ_HOST", zmq_raw.get("host", "0.0.0.0")),
            port=int(os.getenv("ZMQ_PORT", zmq_raw.get("port", 5570))),
            backend_port=int(os.getenv("ZMQ_BACKEND_PORT", zmq_raw.get("backend_port", 5571))),
            max_workers=int(os.getenv("ZMQ_WORKERS", zmq_raw.get("max_workers", 8))),
            rate_limit_per_minute=int(os.getenv("ZMQ_RATE_LIMIT", zmq_raw.get("rate_limit_per_minute", 0))),
            ipc_path=os.getenv("ZMQ_IPC_PATH", zmq_raw.get("ipc_path", "")),
        ),
    )
    return settings
