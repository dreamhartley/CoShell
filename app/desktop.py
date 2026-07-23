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


def _open_windows_clipboard(user32: object, attempts: int = 20) -> None:
    for _ in range(attempts):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.01)
    raise RuntimeError("剪贴板正被其他程序占用")


def _read_windows_clipboard() -> str:
    if os.name != "nt":
        raise RuntimeError("当前平台不支持桌面剪贴板桥接")
    import ctypes
    from ctypes import wintypes

    cf_unicode_text = 13
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    _open_windows_clipboard(user32)
    try:
        if not user32.IsClipboardFormatAvailable(cf_unicode_text):
            return ""
        handle = user32.GetClipboardData(cf_unicode_text)
        if not handle:
            raise RuntimeError("无法读取剪贴板")
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise RuntimeError("无法锁定剪贴板内容")
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _write_windows_clipboard(value: str) -> None:
    if os.name != "nt":
        raise RuntimeError("当前平台不支持桌面剪贴板桥接")
    import ctypes
    from ctypes import wintypes

    cf_unicode_text = 13
    gmem_moveable = 0x0002
    content = (str(value) + "\0").encode("utf-16-le")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    handle = kernel32.GlobalAlloc(gmem_moveable, len(content))
    if not handle:
        raise RuntimeError("无法分配剪贴板内存")
    transferred = False
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise RuntimeError("无法锁定剪贴板内存")
        try:
            ctypes.memmove(pointer, content, len(content))
        finally:
            kernel32.GlobalUnlock(handle)
        _open_windows_clipboard(user32)
        try:
            if not user32.EmptyClipboard() or not user32.SetClipboardData(cf_unicode_text, handle):
                raise RuntimeError("无法写入剪贴板")
            transferred = True
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(handle)


class DesktopApi:
    """Native capabilities exposed only to the embedded desktop webview."""

    def __init__(self) -> None:
        self._clipboard_lock = threading.RLock()

    def read_clipboard(self) -> str:
        with self._clipboard_lock:
            return _read_windows_clipboard()

    def write_clipboard(self, value: str) -> bool:
        with self._clipboard_lock:
            _write_windows_clipboard(value)
        return True


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
            js_api=DesktopApi(),
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
