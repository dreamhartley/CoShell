from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="轻量 SSH 终端")
    parser.add_argument(
        "--web",
        action="store_true",
        help="仅启动 Web 服务（用于开发和调试）",
    )
    args = parser.parse_args()

    if args.web:
        import uvicorn

        uvicorn.run("app.main:app", host="127.0.0.1", port=8765, reload=False)
        return

    from app.desktop import run_desktop

    run_desktop()


if __name__ == "__main__":
    main()
