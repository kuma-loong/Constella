from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .app import create_lab_app
from .config import LabConfig
from .store import LabStore


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="constella-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="run the Access-protected Lab manager")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--frontend-dir", type=Path)
    serve.add_argument("--log-level", default="info")
    backup = subparsers.add_parser("backup", help="create a consistent Lab database backup")
    backup.add_argument("target", type=Path)
    args = parser.parse_args(argv)

    config = LabConfig.from_env()
    if args.command == "serve":
        app = create_lab_app(config=config, frontend_dist=args.frontend_dir)
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
        return

    store = LabStore(config.db_path)
    store.open()
    try:
        store.backup(args.target)
    finally:
        store.close()
