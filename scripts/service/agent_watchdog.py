#!/usr/bin/env python3
"""Recover missing remote agents without changing or deploying application code."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
LOG = logging.getLogger("agent-watchdog")

# Executed through SSH on an already provisioned node. Never signals any agent.
REMOTE_START = r'''
import fcntl, json, os, subprocess, sys
from pathlib import Path

def report(outcome, **details):
    print(json.dumps(dict(outcome=outcome, **details)))

def is_agent(pid, runtime):
    proc = Path('/proc') / str(pid)
    try:
        if proc.stat().st_uid != os.getuid():
            return False
        if proc.joinpath('stat').read_text().split(') ', 1)[1].startswith('Z '):
            return False
        argv = proc.joinpath('cmdline').read_bytes().split(b'\0')
        return (b'-m' in argv and b'constella.agent_main' in argv
                and proc.joinpath('cwd').resolve() == runtime.resolve())
    except OSError:
        return False

base = Path(os.path.expandvars(sys.argv[1])).expanduser()
runtime = base / 'agent/runtime'
starter = base / 'agent/start_agent.sh'
if not starter.is_file() or not runtime.is_dir():
    report('not_provisioned', detail='Existing agent runtime/start script required')
    sys.exit(0)
os.umask(0o077)
with (base / 'run/agent-watchdog.lock').open('a') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        report('busy')
        sys.exit(0)
    pidfile = base / 'run/agent.pid'
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            if pid <= 1:
                raise ValueError('Invalid PID')
        except ValueError:
            report('refused', detail='Invalid existing PID file')
            sys.exit(0)
        proc = Path('/proc') / str(pid)
        if proc.exists() and not proc.joinpath('stat').read_text().split(') ', 1)[1].startswith('Z '):
            if is_agent(pid, runtime):
                report('already_running', pid=pid)
            else:
                report('refused', detail='PID file refers to a different process', pid=pid)
            sys.exit(0)
        # The original start script treats zombies as running; remove only a verified stale PID.
        pidfile.unlink()
    # Protect against a missing PID file while the original agent is still alive.
    for proc in Path('/proc').iterdir():
        if proc.name.isdigit() and is_agent(int(proc.name), runtime):
            report('already_running', pid=int(proc.name))
            sys.exit(0)
    result = subprocess.run(['bash', str(starter)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        report('failed', detail=(result.stderr or result.stdout)[-400:])
    elif pidfile.exists() and is_agent(int(pidfile.read_text()), runtime):
        report('started', pid=int(pidfile.read_text()))
    else:
        report('failed', detail='Start script returned without a verified agent process')
'''


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def load_nodes(path: Path) -> tuple[list[dict], str, float]:
    import yaml

    config = yaml.safe_load(path.read_text())
    if not isinstance(config, dict) or not isinstance(config.get("nodes"), list):
        raise ValueError("nodes config must contain a nodes list")
    nodes = config["nodes"]
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not all(isinstance(node.get(k), str) and node[k] for k in ("id", "host")):
            raise ValueError("Each node needs a nonempty id and host")
        if node["id"] in ids or node["host"].startswith("-"):
            raise ValueError("Duplicate node id or invalid SSH host")
        ids.add(node["id"])
        if node.get("user") is not None and not isinstance(node["user"], str):
            raise ValueError("SSH user must be a string")
        if node.get("port") is not None and not 1 <= int(node["port"]) <= 65535:
            raise ValueError("Invalid SSH port")
    base = config.get("remote_base", "$HOME/.constella")
    refresh = float(config.get("refresh_interval", 1))
    if not isinstance(base, str) or not base or not 0 < refresh <= 60:
        raise ValueError("Invalid remote base or refresh interval")
    return nodes, base, refresh


def manager_healthy(url: str) -> bool:
    # Use the local manager directly, independent of shell HTTP proxy settings.
    with build_opener(ProxyHandler({})).open(url.rstrip("/") + "/api/health", timeout=5) as response:
        data = json.load(response)
    return data.get("ok") is True and data.get("source") == "manager" and data.get("agent_ingest_enabled") is True


async def observe_nodes(url: str, token: str, expected: set[str], seconds: float) -> set[str]:
    import websockets

    address = urlsplit(url)
    stream = urlunsplit(("wss" if address.scheme == "https" else "ws", address.netloc, "/api/highres/stream", "", ""))
    seen: set[str] = set()
    async with websockets.connect(
        stream, additional_headers={"Authorization": f"Bearer {token}"},
        open_timeout=5, close_timeout=2, max_queue=32,
    ) as socket:
        deadline = time.monotonic() + seconds
        while seen != expected:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                message = json.loads(await asyncio.wait_for(socket.recv(), remaining))
            except asyncio.TimeoutError:
                break
            if message.get("type") == "gpu_sample" and message.get("node_id") in expected:
                seen.add(message["node_id"])
    return seen


async def start_remote(node: dict, base: str, timeout: float) -> dict:
    target = f"{node['user']}@{node['host']}" if node.get("user") else node["host"]
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
               "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]
    if node.get("port"):
        command += ["-p", str(node["port"])]
    command += ["--", target, "python3 - " + shlex.quote(base)]
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(REMOTE_START.encode()), timeout)
    except BaseException:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()  # Only the SSH child created above, never an agent.
            await process.wait()
        raise
    if process.returncode:
        return {"outcome": "failed", "detail": stderr.decode(errors="replace")[-400:]}
    try:
        result = json.loads(stdout)
        if not isinstance(result, dict) or "outcome" not in result:
            raise ValueError("Missing startup outcome")
        return result
    except ValueError:
        return {"outcome": "failed", "detail": "Unexpected SSH startup response"}


async def check_once(args) -> dict:
    nodes, base, refresh = load_nodes(args.nodes)
    report = {"checked_at": time.time(), "nodes": {}, "status": "manager_unhealthy"}
    if not await asyncio.to_thread(manager_healthy, args.manager_url):
        return report
    token = args.token_file.read_text().strip()
    if not token:
        raise ValueError("High-resolution stream token file is empty")
    expected = {node["id"] for node in nodes}
    # Observe accepted live samples, not a remote state file left behind by a reboot.
    seen = await observe_nodes(args.manager_url, token, expected, max(args.observe_seconds, 3 * refresh + 2))
    if not await asyncio.to_thread(manager_healthy, args.manager_url):
        return report
    report["status"] = "checked"
    for node in nodes:
        if node["id"] in seen:
            report["nodes"][node["id"]] = {"outcome": "connected"}
        elif args.dry_run:
            report["nodes"][node["id"]] = {"outcome": "would_start"}
        else:
            # Manager may stop during an earlier SSH attempt: recheck before each action.
            if not await asyncio.to_thread(manager_healthy, args.manager_url):
                report["status"] = "manager_unhealthy"
                break
            try:
                result = await start_remote(node, base, args.ssh_timeout)
            except (OSError, asyncio.TimeoutError) as exc:
                result = {"outcome": "failed", "detail": str(exc) or "SSH timeout"}
            report["nodes"][node["id"]] = result
            LOG.info("%s: %s", node["id"], result)
    return report


def process_pid(state_dir: Path) -> int | None:
    pidfile = state_dir / "agent-watchdog.pid"
    try:
        pid = int(pidfile.read_text())
        proc = Path(f"/proc/{pid}")
        if pid <= 1 or proc.joinpath("stat").read_text().split(") ", 1)[1].startswith("Z "):
            return None
        argv = [os.fsdecode(a) for a in proc.joinpath("cmdline").read_bytes().split(b"\0") if a]
        if len(argv) < 3 or Path(argv[1]).resolve() != SCRIPT or argv[2] != "run":
            return None
        directory = Path(argv[argv.index("--state-dir") + 1]) if "--state-dir" in argv else ROOT / "run"
        return pid if directory.resolve() == state_dir.resolve() else None
    except (OSError, ValueError, IndexError):
        return None


async def run_loop(args) -> None:
    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopping.set)
    stop_task = asyncio.create_task(stopping.wait())
    try:
        while not stopping.is_set():
            started = time.monotonic()
            check = asyncio.create_task(check_once(args))
            done, _ = await asyncio.wait({check, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if stop_task in done:
                check.cancel()
                await asyncio.gather(check, return_exceptions=True)
                break
            try:
                report = check.result()
            except Exception as exc:
                report = {"checked_at": time.time(), "status": "probe_failed", "detail": str(exc)}
                LOG.warning("Check skipped: %s", exc)
            delay = max(0, args.interval - (time.monotonic() - started))
            report["next_check_at"] = time.time() + delay
            atomic_json(args.state_dir / "agent-watchdog-state.json", report)
            LOG.info("Check complete: %s", report["status"])
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), delay)
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)


def daemon(args) -> None:
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "agent-watchdog.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("agent watchdog already running")
            return
        pidfile = args.state_dir / "agent-watchdog.pid"
        pidfile.write_text(str(os.getpid()) + "\n")
        pidfile.chmod(0o600)
        try:
            asyncio.run(run_loop(args))
        finally:
            if pidfile.read_text().strip() == str(os.getpid()):
                pidfile.unlink()


def start_daemon(args) -> None:
    pid = process_pid(args.state_dir)
    if pid:
        print(f"agent watchdog already running: pid={pid}")
        return
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(SCRIPT), "run"]
    for option in ("nodes", "manager_url", "token_file", "state_dir", "log_file", "interval", "observe_seconds", "ssh_timeout"):
        value = getattr(args, option)
        command += ["--" + option.replace("_", "-"), str(value.resolve() if isinstance(value, Path) else value)]
    with args.log_file.open("ab") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(30):
        pid = process_pid(args.state_dir)
        if pid:
            print(f"agent watchdog started: pid={pid} interval={args.interval:g}s log={args.log_file}")
            return
        if child.poll() is not None:
            break
        time.sleep(0.1)
    raise RuntimeError("Watchdog did not start; inspect its log")


def stop_daemon(args) -> None:
    pidfile = args.state_dir / "agent-watchdog.pid"
    if not pidfile.exists():
        print("agent watchdog: not running")
        return
    pid = process_pid(args.state_dir)
    if not pid:
        try:
            raw = int(pidfile.read_text())
            alive = Path(f"/proc/{raw}/stat").read_text().split(") ", 1)[1][0] != "Z"
        except (OSError, ValueError):
            alive = False
        if alive:
            raise RuntimeError("PID file belongs to a different process; refusing to signal it")
        pidfile.unlink()
        print("agent watchdog: removed stale PID file")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(60):
        if process_pid(args.state_dir) != pid:
            print(f"agent watchdog stopped: pid={pid}")
            return
        time.sleep(0.1)
    raise RuntimeError("Watchdog did not stop; no forced signal sent")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=["start", "stop", "status", "run", "once"])
    result.add_argument("--nodes", type=Path, default=Path(os.environ.get("NODES_CONFIG", ROOT / "nodes.yaml")))
    result.add_argument("--manager-url", default="http://127.0.0.1:8765")
    result.add_argument("--token-file", type=Path, default=ROOT / "run/highres-token")
    result.add_argument("--state-dir", type=Path, default=ROOT / "run")
    result.add_argument("--log-file", type=Path, default=ROOT / "logs/agent-watchdog.log")
    result.add_argument("--interval", type=float, default=300)
    result.add_argument("--observe-seconds", type=float, default=15)
    result.add_argument("--ssh-timeout", type=float, default=30)
    result.add_argument("--dry-run", action="store_true", help="once only: report missing nodes without SSH")
    return result


def main() -> None:
    args = parser().parse_args()
    os.umask(0o077)
    if any(not math.isfinite(value) or value <= 0
           for value in (args.interval, args.observe_seconds, args.ssh_timeout)):
        raise ValueError("Intervals and timeouts must be positive")
    url = urlsplit(args.manager_url)
    if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password:
        raise ValueError("Manager URL must be an HTTP(S) origin without credentials")
    if args.dry_run and args.command != "once":
        raise ValueError("--dry-run requires once")
    if args.command == "run":
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[RotatingFileHandler(args.log_file, maxBytes=2*1024*1024, backupCount=2)])
        daemon(args)
    elif args.command == "start":
        load_nodes(args.nodes)
        start_daemon(args)
    elif args.command == "stop":
        stop_daemon(args)
    elif args.command == "status":
        pid = process_pid(args.state_dir)
        print(f"agent watchdog: running pid={pid}" if pid else "agent watchdog: not running")
        state = args.state_dir / "agent-watchdog-state.json"
        if state.exists():
            print(state.read_text().strip())
    else:
        print(json.dumps(asyncio.run(check_once(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"agent watchdog: {error}", file=sys.stderr)
        sys.exit(1)
