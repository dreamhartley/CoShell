import os
from pathlib import Path
from urllib.request import urlopen


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


class FakeWindow:
    def __init__(self):
        self.width = 1440
        self.height = 900
        self.confirm_close = False
        self.native = type("Native", (), {"scale_factor": 2})()
        self.events = type("Events", (), {"closing": FakeEvent()})()


def test_desktop_launcher_serves_app_and_stops(monkeypatch, tmp_path):
    import webview

    captured = {}
    fake_window = FakeWindow()

    def create_window(title, url, **kwargs):
        captured.update(title=title, url=url, options=kwargs)
        fake_window.confirm_close = kwargs["confirm_close"]
        return fake_window

    def start(**kwargs):
        with urlopen(captured["url"] + "/api/status", timeout=3) as response:
            assert response.status == 200
            assert b"vault_initialized" in response.read()
        captured["start_options"] = kwargs
        fake_window.events.closing.fire()
        assert fake_window.confirm_close is False
        fake_window.width = 2560
        fake_window.height = 1520
        captured["options"]["js_api"].set_active_connections(1)
        fake_window.events.closing.fire()
        assert fake_window.confirm_close is True

    monkeypatch.setattr(webview, "create_window", create_window)
    monkeypatch.setattr(webview, "start", start)
    monkeypatch.setenv("WEBSSH_DATA_DIR", str(tmp_path))

    from app.desktop import run_desktop

    run_desktop()

    assert captured["title"] == "CoShell"
    assert captured["url"].startswith("http://127.0.0.1:")
    assert captured["options"]["width"] == 1440
    assert captured["options"]["height"] == 900
    assert captured["options"]["min_size"] == (960, 640)
    assert captured["options"]["js_api"].read_clipboard
    assert captured["options"]["js_api"].write_clipboard
    assert captured["options"]["js_api"].has_active_connections()
    assert captured["options"]["localization"]["global.quitConfirmation"] == "仍有主机保持连接，确定退出吗？"
    assert captured["start_options"]["private_mode"] is False
    assert captured["start_options"]["icon"].endswith("assets\\app-icon.ico")
    from app.desktop import _load_window_size
    assert _load_window_size() == (1280, 760)


def test_window_size_is_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBSSH_DATA_DIR", str(tmp_path))
    from app.desktop import _load_window_size, _save_window_size

    _save_window_size(1680, 1050)

    assert _load_window_size() == (1680, 1050)


def test_desktop_clipboard_api_serializes_native_access(monkeypatch):
    import app.desktop as desktop

    values = []
    monkeypatch.setattr(desktop, "_read_windows_clipboard", lambda: "clipboard text")
    monkeypatch.setattr(desktop, "_write_windows_clipboard", values.append)
    api = desktop.DesktopApi()

    assert api.read_clipboard() == "clipboard text"
    assert api.write_clipboard("new text") is True
    assert values == ["new text"]


def test_desktop_api_tracks_active_connections():
    from app.desktop import DesktopApi

    api = DesktopApi()

    assert api.has_active_connections() is False
    assert api.set_active_connections(2) is True
    assert api.has_active_connections() is True
    assert api.set_active_connections(0) is True
    assert api.has_active_connections() is False


def test_physical_window_size_is_normalized_for_high_dpi():
    from app.desktop import _logical_window_size

    window = type("Window", (), {
        "width": 2880,
        "height": 1800,
        "native": type("Native", (), {"scale_factor": 2})(),
    })()

    assert _logical_window_size(window) == (1440, 900)


def test_legacy_physical_pixel_window_state_is_migrated(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBSSH_DATA_DIR", str(tmp_path))
    (tmp_path / "window-state.json").write_text('{"version":1,"width":3868,"height":2188}', encoding="utf-8")
    import app.desktop as desktop
    monkeypatch.setattr(desktop, "_display_scale_factor", lambda: 2)

    assert desktop._load_window_size() == (1934, 1094)


def test_packaged_data_dir_is_next_to_executable(monkeypatch, tmp_path):
    import app.desktop as desktop

    executable = tmp_path / "CoShell.exe"
    monkeypatch.delenv("WEBSSH_DATA_DIR", raising=False)
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(executable))

    desktop._configure_packaged_data_dir()

    assert Path(os.environ["WEBSSH_DATA_DIR"]) == tmp_path / "data"


def test_packaged_data_dir_respects_explicit_override(monkeypatch, tmp_path):
    import app.desktop as desktop

    override = tmp_path / "custom-data"
    monkeypatch.setenv("WEBSSH_DATA_DIR", str(override))
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "executable", str(tmp_path / "CoShell.exe"))

    desktop._configure_packaged_data_dir()

    assert Path(os.environ["WEBSSH_DATA_DIR"]) == override
