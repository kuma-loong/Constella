from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import uvicorn

from . import __version__
from .agent import AgentConfig, run_agent
from .cluster_control import ClusterController, format_results, load_cluster_config
from .collector import DEVICE_TYPES, validate_refresh_interval
from .db import RAW_SNAPSHOT_RETENTION_SECONDS, SQLiteStore
from .highres_sidecar import HighresSidecarConfig
from .nvml import sample_with_fallback
from .paths import default_build_root, default_project_root
from .service import ServiceConfig, format_service_results, start_service, status_service, stop_service

PROJECT_ROOT = default_project_root()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="constella")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="run the web service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--refresh", type=float, default=1.0)
    serve.add_argument("--process-refresh", type=float, default=5.0)
    serve.add_argument("--graceful-timeout", type=float, default=10.0)
    serve.add_argument("--agent-token-file", type=Path)
    serve.add_argument("--highres-token-file", type=Path)
    serve.add_argument("--db-path", type=Path)
    serve.add_argument("--db-queue-size", type=int)
    serve.add_argument("--raw-snapshot-seconds", type=float)
    serve.add_argument("--frontend-dir", type=Path)
    serve.add_argument("--log-level", default="info")

    highres_sidecar = subparsers.add_parser(
        "highres-sidecar",
        help="run the high-resolution job curve sidecar",
    )
    highres_sidecar.add_argument("--host", default="127.0.0.1")
    highres_sidecar.add_argument("--port", type=int, default=8766)
    highres_sidecar.add_argument("--db-path", type=Path)
    highres_sidecar.add_argument(
        "--manager-stream-url",
        default="ws://127.0.0.1:8765/api/highres/stream",
    )
    highres_sidecar.add_argument("--token-file", type=Path)
    highres_sidecar.add_argument("--retention-seconds", type=float)
    highres_sidecar.add_argument("--graceful-timeout", type=float, default=10.0)
    highres_sidecar.add_argument("--log-level", default="info")

    probe = subparsers.add_parser("probe", help="print one JSON GPU snapshot")
    probe.add_argument("--pretty", action="store_true")

    agent = subparsers.add_parser("agent", help="run a GPU node agent")
    agent.add_argument("--node-id")
    agent.add_argument("--manager-url")
    agent.add_argument("--token-file")
    agent.add_argument("--refresh", type=float)
    agent.add_argument("--process-refresh", type=float)
    agent.add_argument("--state-file", type=Path)
    agent.add_argument("--device", choices=DEVICE_TYPES)

    cluster = subparsers.add_parser("cluster", help="manage remote GPU node agents")
    cluster_subparsers = cluster.add_subparsers(dest="cluster_command")

    cluster_start = cluster_subparsers.add_parser("start", help="start agents from nodes.yaml")
    cluster_start.add_argument("--nodes", type=Path, default=Path("nodes.yaml"))
    cluster_start.add_argument("--no-sync", action="store_true")

    cluster_status = cluster_subparsers.add_parser("status", help="check remote agent status")
    cluster_status.add_argument("--nodes", type=Path, default=Path("nodes.yaml"))

    cluster_stop = cluster_subparsers.add_parser("stop", help="stop remote agents")
    cluster_stop.add_argument("--nodes", type=Path, default=Path("nodes.yaml"))

    service = subparsers.add_parser("service", help="start, stop, and inspect a local service stack")
    service_subparsers = service.add_subparsers(dest="service_command")

    service_start = service_subparsers.add_parser("start", help="start manager and optional helpers")
    add_service_common_args(service_start)
    service_start.add_argument("--no-local-agent", action="store_true")
    service_start.add_argument("--local-agent-node-id")
    service_start.add_argument("--local-agent-manager-url")
    service_start.add_argument("--manager-hostname")
    service_start.add_argument("--agent-token-file", type=Path)
    service_start.add_argument("--db-path", type=Path)
    service_start.add_argument("--no-db", action="store_true")
    service_start.add_argument("--db-queue-size", type=int, default=1024)
    service_start.add_argument("--raw-snapshot-seconds", type=float, default=0.0)
    service_start.add_argument("--frontend-dir", type=Path)
    service_start.add_argument("--highres-sidecar", action="store_true")
    service_start.add_argument("--highres-host", default="127.0.0.1")
    service_start.add_argument("--highres-port", type=int, default=8766)
    service_start.add_argument("--highres-token-file", type=Path)
    service_start.add_argument("--highres-manager-stream-url")
    service_start.add_argument("--highres-retention-seconds", type=float)
    service_start.add_argument("--cluster-nodes", type=Path)
    service_start.add_argument("--cluster-no-sync", action="store_true")
    service_start.add_argument("--wait-timeout", type=float, default=10.0)

    service_status = service_subparsers.add_parser("status", help="show service process status")
    add_service_common_args(service_status, include_runtime=False)
    service_status.add_argument("--cluster-nodes", type=Path)

    service_stop = service_subparsers.add_parser("stop", help="stop service processes")
    add_service_common_args(service_stop, include_runtime=False)
    service_stop.add_argument("--cluster-nodes", type=Path)
    service_stop.add_argument(
        "--stop-cluster",
        action="store_true",
        help="also stop remote agents from --cluster-nodes",
    )

    db = subparsers.add_parser("db", help="maintain the optional SQLite database")
    db_subparsers = db.add_subparsers(dest="db_command")

    db_maintain = db_subparsers.add_parser("maintain", help="run routine SQLite maintenance")
    db_maintain.add_argument("--path", type=Path, default=Path("run/constella.db"))
    db_maintain.add_argument("--raw-retention-seconds", type=float, default=RAW_SNAPSHOT_RETENTION_SECONDS)
    db_maintain.add_argument("--session-stale-seconds", type=float, default=300.0)

    db_rollup = db_subparsers.add_parser("rollup", help="roll up stored GPU metric rollups")
    db_rollup.add_argument("--path", type=Path, default=Path("run/constella.db"))
    db_rollup.add_argument("--from-bucket-seconds", type=int, required=True)
    db_rollup.add_argument("--to-bucket-seconds", type=int, required=True)

    db_migrate_samples = db_subparsers.add_parser(
        "migrate-samples",
        help="one-time migration from legacy raw GPU samples to 20s rollups",
    )
    db_migrate_samples.add_argument("--path", type=Path, default=Path("run/constella.db"))
    db_migrate_samples.add_argument("--bucket-seconds", type=int, default=20)

    db_prune_rollups = db_subparsers.add_parser("prune-rollups", help="delete expired rollups")
    db_prune_rollups.add_argument("--path", type=Path, default=Path("run/constella.db"))
    db_prune_rollups.add_argument("--bucket-seconds", type=int)

    db_prune_raw = db_subparsers.add_parser("prune-raw", help="delete expired raw snapshots")
    db_prune_raw.add_argument("--path", type=Path, default=Path("run/constella.db"))
    db_prune_raw.add_argument(
        "--retention-seconds",
        type=float,
        default=RAW_SNAPSHOT_RETENTION_SECONDS,
    )

    db_close_sessions = db_subparsers.add_parser(
        "close-sessions",
        help="close long-unseen running process sessions",
    )
    db_close_sessions.add_argument("--path", type=Path, default=Path("run/constella.db"))
    db_close_sessions.add_argument("--stale-seconds", type=float, default=60.0)

    args = parser.parse_args(argv)

    if args.command == "serve":
        try:
            refresh = validate_refresh_interval(args.refresh)
        except ValueError as exc:
            parser.error(str(exc))
        os.environ["CONSTELLA_REFRESH_SECONDS"] = str(refresh)
        os.environ["CONSTELLA_PROCESS_SECONDS"] = str(args.process_refresh)
        if args.agent_token_file is not None:
            os.environ["CONSTELLA_AGENT_TOKEN_FILE"] = str(args.agent_token_file)
        if args.highres_token_file is not None:
            os.environ["CONSTELLA_HIGHRES_TOKEN_FILE"] = str(args.highres_token_file)
        if args.db_path is not None:
            os.environ["CONSTELLA_DB_PATH"] = str(args.db_path)
        if args.db_queue_size is not None:
            os.environ["CONSTELLA_DB_QUEUE_SIZE"] = str(args.db_queue_size)
        if args.raw_snapshot_seconds is not None:
            os.environ["CONSTELLA_RAW_SNAPSHOT_SECONDS"] = str(args.raw_snapshot_seconds)
        if args.frontend_dir is not None:
            os.environ["CONSTELLA_FRONTEND_DIST"] = str(args.frontend_dir)
        uvicorn.run(
            "constella.app:create_app",
            host=args.host,
            port=args.port,
            factory=True,
            log_level=args.log_level,
            lifespan="on",
            timeout_graceful_shutdown=max(0.0, args.graceful_timeout),
        )
        return

    if args.command == "highres-sidecar":
        try:
            config = HighresSidecarConfig.from_env(
                db_path=args.db_path,
                manager_stream_url=args.manager_stream_url,
                token_file=args.token_file,
                retention_seconds=args.retention_seconds,
            )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        os.environ["CONSTELLA_DB_PATH"] = str(config.db_path)
        os.environ["CONSTELLA_HIGHRES_MANAGER_STREAM_URL"] = config.manager_stream_url
        if config.token:
            os.environ["CONSTELLA_HIGHRES_TOKEN"] = config.token
        os.environ["CONSTELLA_HIGHRES_RETENTION_SECONDS"] = str(config.retention_seconds)
        uvicorn.run(
            "constella.highres_sidecar:create_highres_sidecar_app",
            host=args.host,
            port=args.port,
            factory=True,
            log_level=args.log_level,
            lifespan="on",
            timeout_graceful_shutdown=max(0.0, args.graceful_timeout),
        )
        return

    if args.command == "probe":
        snapshot = sample_with_fallback()
        json.dump(
            snapshot.to_dict(),
            sys.stdout,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
        sys.stdout.write("\n")
        return

    if args.command == "agent":
        try:
            config = AgentConfig.from_env(
                node_id=args.node_id,
                manager_url=args.manager_url,
                token_file=args.token_file,
                refresh_interval=args.refresh,
                process_interval=args.process_refresh,
                state_file=args.state_file,
                device_type=args.device,
            )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        asyncio.run(run_agent(config))
        return

    if args.command == "cluster":
        if not args.cluster_command:
            cluster.print_help()
            return
        try:
            config = load_cluster_config(args.nodes)
        except (OSError, KeyError, ValueError) as exc:
            parser.error(str(exc))
        controller = ClusterController(
            config,
            project_root=PROJECT_ROOT,
            build_root=default_build_root(),
            sync_source=not getattr(args, "no_sync", False),
        )
        if args.cluster_command == "start":
            results = controller.start_all()
        elif args.cluster_command == "status":
            results = controller.status_all()
        elif args.cluster_command == "stop":
            results = controller.stop_all()
        else:
            parser.error(f"unknown cluster command: {args.cluster_command}")
        print(format_results(results))
        if any(not result.ok for result in results):
            sys.exit(1)
        return

    if args.command == "service":
        if not args.service_command:
            service.print_help()
            return
        try:
            config = service_config_from_args(args)
            if args.service_command == "start":
                results = start_service(config)
            elif args.service_command == "status":
                results = status_service(config, include_cluster=args.cluster_nodes is not None)
            elif args.service_command == "stop":
                results = stop_service(config, stop_cluster=args.stop_cluster)
            else:
                parser.error(f"unknown service command: {args.service_command}")
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        print(format_service_results(results))
        if any(not result.ok for result in results):
            sys.exit(1)
        return

    if args.command == "db":
        if not args.db_command:
            db.print_help()
            return
        store = SQLiteStore(args.path)
        store.open()
        try:
            if args.db_command == "maintain":
                result = store.maintain(
                    raw_retention_seconds=args.raw_retention_seconds,
                    stale_session_seconds=args.session_stale_seconds,
                )
                for key, value in result.items():
                    print(f"{key}: {value}")
            elif args.db_command == "rollup":
                count = store.rollup_gpu_metric_rollups(
                    from_bucket_seconds=args.from_bucket_seconds,
                    to_bucket_seconds=args.to_bucket_seconds,
                )
                print(
                    "rolled up "
                    f"{count} GPU buckets "
                    f"{args.from_bucket_seconds}s -> {args.to_bucket_seconds}s"
                )
            elif args.db_command == "migrate-samples":
                count = store.rollup_legacy_gpu_metric_samples(
                    bucket_seconds=args.bucket_seconds,
                )
                print(f"migrated {count} legacy GPU sample buckets")
            elif args.db_command == "prune-rollups":
                count = store.prune_rollups(bucket_seconds=args.bucket_seconds)
                print(f"deleted {count} expired rollups")
            elif args.db_command == "prune-raw":
                count = store.prune_raw_snapshots(retention_seconds=args.retention_seconds)
                print(f"deleted {count} raw snapshots")
            elif args.db_command == "close-sessions":
                count = store.close_stale_sessions(
                    now=time.time(),
                    stale_after_seconds=args.stale_seconds,
                )
                print(f"closed {count} process sessions")
            else:
                parser.error(f"unknown db command: {args.db_command}")
        finally:
            store.close()
        return

    parser.print_help()


def add_service_common_args(parser: argparse.ArgumentParser, *, include_runtime: bool = True) -> None:
    if include_runtime:
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8765)
        parser.add_argument("--refresh", type=float, default=1.0)
        parser.add_argument("--process-refresh", type=float, default=5.0)
        parser.add_argument("--log-level", default="info")
    else:
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--run-dir", type=Path, default=Path("run"))
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))


def service_config_from_args(args: argparse.Namespace) -> ServiceConfig:
    db_path = getattr(args, "db_path", None)
    if getattr(args, "service_command", None) == "start" and db_path is None:
        db_path = args.run_dir / "constella.db"
    if getattr(args, "no_db", False):
        db_path = None
    return ServiceConfig(
        host=args.host,
        port=args.port,
        refresh=getattr(args, "refresh", 1.0),
        process_refresh=getattr(args, "process_refresh", 5.0),
        log_level=getattr(args, "log_level", "info"),
        run_dir=args.run_dir,
        log_dir=args.log_dir,
        local_agent=not getattr(args, "no_local_agent", False),
        local_agent_node_id=getattr(args, "local_agent_node_id", None),
        local_agent_manager_url=getattr(args, "local_agent_manager_url", None),
        manager_hostname=getattr(args, "manager_hostname", None),
        agent_token_file=getattr(args, "agent_token_file", None),
        db_path=db_path,
        db_queue_size=getattr(args, "db_queue_size", 1024),
        raw_snapshot_seconds=getattr(args, "raw_snapshot_seconds", 0.0),
        frontend_dir=getattr(args, "frontend_dir", None),
        highres_sidecar=getattr(args, "highres_sidecar", False),
        highres_host=getattr(args, "highres_host", "127.0.0.1"),
        highres_port=getattr(args, "highres_port", 8766),
        highres_token_file=getattr(args, "highres_token_file", None),
        highres_manager_stream_url=getattr(args, "highres_manager_stream_url", None),
        highres_retention_seconds=getattr(args, "highres_retention_seconds", None),
        cluster_nodes=getattr(args, "cluster_nodes", None),
        cluster_no_sync=getattr(args, "cluster_no_sync", False),
        wait_timeout=getattr(args, "wait_timeout", 10.0),
    )


if __name__ == "__main__":
    main()
