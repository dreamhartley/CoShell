"""Desktop launcher for the local FastAPI application."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path


def _resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / relative


def _configure_packaged_data_dir() -> None:
    """Keep user data outside PyInstaller's temporary extraction directory."""
    if not getattr(sys, "frozen", False) or os.environ.get("WEBSSH_DATA_DIR"):
        return
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    os.environ["WEBSSH_DATA_DIR"] = str(root / "LightSSHTerminal")


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(server: object, thread: threading.Thread, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bool(getattr(server, "started", False)):
            return
        if not thread.is_alive():
            break
        time.sleep(0.05)
    raise RuntimeError("本地服务启动失败，请查看终端中的错误信息")


def run_desktop() -> None:
    _configure_packaged_data_dir()

    try:
        import uvicorn
        import webview
    except ImportError as exc:
        raise RuntimeError("桌面 GUI 依赖尚未安装，请先运行 start.ps1") from exc

    port = _available_port()
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server_thread = threading.Thread(target=server.run, name="local-web-server", daemon=True)
    server_thread.start()
    _wait_for_server(server, server_thread)

    try:
        webview.create_window(
            "轻量 SSH 终端",
            f"http://127.0.0.1:{port}",
            width=1440,
            height=900,
            min_size=(960, 640),
            confirm_close=False,
        )
        webview.start(
            private_mode=False,
            storage_path=str(Path(os.environ.get("WEBSSH_DATA_DIR", "data")) / "webview"),
            icon=str(_resource_path("assets/app-icon.ico")),
        )
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
