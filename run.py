from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="CoShell")
    parser.add_argument(
        "--web",
        action="store_true",
        help="仅启动 Web 服务（用于开发和调试）",
    )
    parser.add_argument("--searxng-sidecar", nargs=2, metavar=("PORT", "SETTINGS"), help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.searxng_sidecar:
        from app.searxng_backend import run_sidecar

        port, settings_path = args.searxng_sidecar
        run_sidecar(int(port), settings_path)
        return

    if args.web:
        import uvicorn

        uvicorn.run("app.main:app", host="127.0.0.1", port=8765, reload=False)
        return

    from app.desktop import run_desktop

    run_desktop()


if __name__ == "__main__":
    main()
