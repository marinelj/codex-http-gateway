from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent HTTP gateway for the Codex CLI")
    parser.add_argument("command", nargs="?", choices=["serve"], default="serve")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--workspace")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    overrides = {
        key: value for key, value in vars(args).items() if value is not None and key != "command"
    }
    settings = Settings(**overrides)
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, root_path=settings.root_path
    )


if __name__ == "__main__":
    main()
