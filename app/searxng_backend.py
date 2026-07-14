"""Lifecycle and launcher for the bundled, loopback-only SearXNG sidecar."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


SEARXNG_REVISION = "62a1ab7eddc84e98e97605e0a1378e806de6185c"


def _application_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def _source_root() -> Path:
    return _application_root() / "third_party" / "searxng"


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_settings(path: Path, port: int) -> None:
    """Write a private configuration owned entirely by the desktop app."""
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    path.write_text(
        "\n".join(
            [
                "use_default_settings:",
                "  engines:",
                "    keep_only:",
                "      - brave",
                "      - startpage",
                "      - duckduckgo",
                "general:",
                "  debug: false",
                '  instance_name: "CoShell Search"',
                "search:",
                "  safe_search: 1",
                "  autocomplete: \"\"",
                "  formats:",
                "    - json",
                "server:",
                f'  secret_key: "{secret}"',
                '  bind_address: "127.0.0.1"',
                f"  port: {port}",
                "  limiter: false",
                "  public_instance: false",
                "  image_proxy: false",
                "outgoing:",
                "  request_timeout: 6.0",
                "  max_request_timeout: 12.0",
                "  retries: 1",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_sidecar(port: int, settings_path: str) -> None:
    """Run the bundled SearXNG web app in the dedicated child process."""
    source_root = _source_root()
    if not (source_root / "searx" / "webapp.py").is_file():
        raise RuntimeError(f"内置 SearXNG 运行时缺失：{source_root}")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source_root))
    os.environ["SEARXNG_SETTINGS_PATH"] = str(Path(settings_path).resolve())
    os.environ["SEARXNG_BIND_ADDRESS"] = "127.0.0.1"
    os.environ["SEARXNG_PORT"] = str(port)
    os.environ["SEARXNG_LIMITER"] = "false"
    from searx.webapp import run  # type: ignore[import-not-found]  # AGPL sidecar

    run()


class SearxNGService:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.url = ""

    def _command(self, port: int, settings_path: Path) -> list[str]:
        arguments = ["--searxng-sidecar", str(port), str(settings_path)]
        if getattr(sys, "frozen", False):
            return [sys.executable, *arguments]
        return [sys.executable, str(_application_root() / "run.py"), *arguments]

    def start(self, data_dir: Path, timeout: float = 20.0) -> str:
        if self.process and self.process.poll() is None:
            return self.url
        port = _available_port()
        settings_path = data_dir / "searxng" / "settings.yml"
        _write_settings(settings_path, port)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            self._command(port, settings_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self.url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + timeout
        probe = self.url + "/healthz"
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"内置 SearXNG 启动失败（退出码 {self.process.returncode}）")
            try:
                with urllib.request.urlopen(probe, timeout=2) as response:
                    if response.status == 200 and response.read(16).strip() == b"OK":
                        os.environ["WEBSSH_SEARXNG_URL"] = self.url
                        return self.url
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("内置 SearXNG 启动超时")

    def stop(self) -> None:
        process, self.process = self.process, None
        os.environ.pop("WEBSSH_SEARXNG_URL", None)
        self.url = ""
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
