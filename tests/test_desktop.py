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
        self.events = type("Events", (), {"closing": FakeEvent()})()


def test_desktop_launcher_serves_app_and_stops(monkeypatch, tmp_path):
    import webview

    captured = {}
    fake_window = FakeWindow()

    def create_window(title, url, **kwargs):
        captured.update(title=title, url=url, options=kwargs)
        return fake_window

    def start(**kwargs):
        with urlopen(captured["url"] + "/api/status", timeout=3) as response:
            assert response.status == 200
            assert b"vault_initialized" in response.read()
        captured["start_options"] = kwargs
        fake_window.width = 1280
        fake_window.height = 760
        fake_window.events.closing.fire()

    monkeypatch.setattr(webview, "create_window", create_window)
    monkeypatch.setattr(webview, "start", start)
    monkeypatch.setenv("WEBSSH_DATA_DIR", str(tmp_path))

    from app.desktop import run_desktop

    run_desktop()

    assert captured["title"] == "轻量 SSH 终端"
    assert captured["url"].startswith("http://127.0.0.1:")
    assert captured["options"]["width"] == 1440
    assert captured["options"]["height"] == 900
    assert captured["options"]["min_size"] == (960, 640)
    assert captured["start_options"]["private_mode"] is False
    assert captured["start_options"]["icon"].endswith("assets\\app-icon.ico")
    from app.desktop import _load_window_size
    assert _load_window_size() == (1280, 760)


def test_window_size_is_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBSSH_DATA_DIR", str(tmp_path))
    from app.desktop import _load_window_size, _save_window_size

    _save_window_size(1680, 1050)

    assert _load_window_size() == (1680, 1050)


def test_legacy_resize_event_state_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBSSH_DATA_DIR", str(tmp_path))
    (tmp_path / "window-state.json").write_text('{"width":1560,"height":960}', encoding="utf-8")
    from app.desktop import _load_window_size

    assert _load_window_size() == (1440, 900)
