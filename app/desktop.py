"""Desktop launcher for the local FastAPI application."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path


DEFAULT_WINDOW_SIZE = (1440, 900)
MIN_WINDOW_SIZE = (960, 640)
WINDOW_STATE_VERSION = 2


def _data_dir() -> Path:
    return Path(os.environ.get("WEBSSH_DATA_DIR", "data"))


def _display_scale_factor() -> float:
    if os.name != "nt":
        return 1
    try:
        from ctypes import windll

        scale = float(windll.shcore.GetScaleFactorForDevice(0)) / 100
        return scale if 0.5 <= scale <= 4 else 1
    except (AttributeError, OSError, TypeError, ValueError):
        return 1


def _load_window_size() -> tuple[int, int]:
    try:
        value = json.loads((_data_dir() / "window-state.json").read_text(encoding="utf-8"))
        version = value.get("version")
        if version not in (1, WINDOW_STATE_VERSION):
            return DEFAULT_WINDOW_SIZE
        width, height = int(value["width"]), int(value["height"])
        if version == 1:
            scale = _display_scale_factor()
            width, height = round(width / scale), round(height / scale)
        if width >= MIN_WINDOW_SIZE[0] and height >= MIN_WINDOW_SIZE[1]:
            return width, height
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return DEFAULT_WINDOW_SIZE


def _save_window_size(width: int, height: int) -> None:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "window-state.json"
    temporary = data_dir / "window-state.tmp"
    temporary.write_text(
        json.dumps({"version": WINDOW_STATE_VERSION, "width": int(width), "height": int(height)}),
        encoding="utf-8",
    )
    temporary.replace(target)


def _logical_window_size(window: object) -> tuple[int, int]:
    """Convert pywebview's WinForms physical pixels back to logical pixels."""
    width, height = int(getattr(window, "width")), int(getattr(window, "height"))
    native = getattr(window, "native", None)
    try:
        scale = float(getattr(native, "scale_factor", 1) or 1)
    except (TypeError, ValueError):
        scale = 1
    if not 0.5 <= scale <= 4:
        scale = 1
    return max(1, round(width / scale)), max(1, round(height / scale))


def _resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / relative


def _configure_packaged_data_dir() -> None:
    """Keep user data outside PyInstaller's temporary extraction directory."""
    if not getattr(sys, "frozen", False) or os.environ.get("WEBSSH_DATA_DIR"):
        return
    os.environ["WEBSSH_DATA_DIR"] = str(Path(sys.executable).resolve().parent / "data")


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
        width, height = _load_window_size()
        window = webview.create_window(
            "CoShell",
            f"http://127.0.0.1:{port}",
            width=width,
            height=height,
            min_size=MIN_WINDOW_SIZE,
            confirm_close=False,
        )
        if window is not None and hasattr(window, "events"):
            # WinForms reports physical pixels while create_window expects
            # logical pixels. Persist the normalized final dimensions from the
            # locking closing event so resize worker threads cannot race.
            window.events.closing += lambda: _save_window_size(*_logical_window_size(window))
        webview.start(
            private_mode=False,
            storage_path=str(_data_dir() / "webview"),
            icon=str(_resource_path("assets/app-icon.ico")),
        )
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)
