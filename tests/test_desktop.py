from urllib.request import urlopen


def test_desktop_launcher_serves_app_and_stops(monkeypatch):
    import webview

    captured = {}

    def create_window(title, url, **kwargs):
        captured.update(title=title, url=url, options=kwargs)

    def start(**kwargs):
        with urlopen(captured["url"] + "/api/status", timeout=3) as response:
            assert response.status == 200
            assert b"vault_initialized" in response.read()
        captured["start_options"] = kwargs

    monkeypatch.setattr(webview, "create_window", create_window)
    monkeypatch.setattr(webview, "start", start)

    from app.desktop import run_desktop

    run_desktop()

    assert captured["title"] == "轻量 SSH 终端"
    assert captured["url"].startswith("http://127.0.0.1:")
    assert captured["options"]["min_size"] == (960, 640)
    assert captured["start_options"]["private_mode"] is False
    assert captured["start_options"]["icon"].endswith("assets\\app-icon.ico")
