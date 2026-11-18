"""Measure isolated Agent and Manager processes with the same live workload."""

import argparse
import asyncio
from contextlib import ExitStack, closing
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import statistics
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import websockets


ROOT = Path(__file__).resolve().parents[2]
PORT = 8875
URL = f"http://127.0.0.1:{PORT}"
NODE_ID = "preview-benchmark"


def read_process(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    ticks = int(fields[11]) + int(fields[12])
    rss_kib = int(fields[21]) * os.sysconf("SC_PAGESIZE") // 1024
    return ticks, rss_kib


def stop(process, argv):
    if process.poll() is not None:
        return
    command = Path(f"/proc/{process.pid}/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
    expected = [os.fsencode(arg) for arg in argv]
    if command != expected and not (command[1:] == expected and b"python" in command[0]):
        raise RuntimeError(f"Unexpected preview command line for PID {process.pid}")
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def launch(stack, directory, env, name, argv):
    log = stack.enter_context((directory / f"{name}.log").open("wb"))
    process = subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    stack.callback(stop, process, argv)
    return process


def request(path):
    with urlopen(URL + path, timeout=10) as response:
        return json.load(response)


async def wait_ready(manager):
    for _ in range(100):
        if manager.poll() is not None:
            raise RuntimeError("Manager exited during startup")
        try:
            if request("/api/health")["ok"]:
                return
        except OSError:
            pass
        await asyncio.sleep(0.1)
    raise RuntimeError("Manager health probe timed out")


def optional_query(path):
    measurements = []
    for _ in range(20):
        start = time.perf_counter()
        try:
            request(path)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise
        measurements.append((time.perf_counter() - start) * 1000)
    return dist(measurements)


def analytics_query(path):
    start = time.perf_counter()
    try:
        request(path)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    cold = round((time.perf_counter() - start) * 1000, 3)
    return {"cold": cold, "warm": optional_query(path)}


def dist(values):
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
    }


async def observe(ws, processes, duration):
    async def next_sample(after):
        while True:
            snapshot = json.loads(await asyncio.wait_for(ws.recv(), 10))
            for node in snapshot["nodes"]:
                if node["node_id"] == NODE_ID and node["status"] == "online" and node["seq"] > after:
                    if node["gpus"]:
                        return node

    initial = await asyncio.wait_for(next_sample(-1), 15)
    start = time.monotonic()
    counters = {name: read_process(process.pid) for name, process in processes.items()}
    last = initial["seq"]
    samples = []
    rss = {name: [] for name in processes}
    while time.monotonic() - start < duration:
        node = await asyncio.wait_for(next_sample(last), 15)
        last = node["seq"]
        samples.append(node)
        for name, process in processes.items():
            rss[name].append(read_process(process.pid)[1])
    runtime = time.monotonic() - start
    frequency = os.sysconf("SC_CLK_TCK")
    usage = {
        name: {
            "cpu_percent": round(100 * (read_process(process.pid)[0] - counters[name][0]) / frequency / runtime, 3),
            "rss_kib": dist(rss[name]),
        }
        for name, process in processes.items()
    }
    span = samples[-1]["sampled_at"] - initial["sampled_at"]
    expected = max(len(samples), round(span / initial["refresh_interval"]))
    return {
        "gpu_count": len(initial["gpus"]),
        "gpu_name": initial["gpus"][0]["name"],
        "degraded_gpu_samples": sum(
            bool(gpu["error"] or gpu.get("telemetry_errors")) for node in samples for gpu in node["gpus"]
        ),
        "process_count_at_start": initial["totals"]["active_processes"],
        "observed_seconds": round(runtime, 3),
        "observed_samples": len(samples),
        "expected_samples": expected,
        "missing_sample_rate": round((expected - len(samples)) / expected, 6),
        "skipped_sequence_numbers": last - initial["seq"] - len(samples),
        "sample_elapsed_ms": dist([node["elapsed_ms"] for node in samples]),
        "performance": {
            str(gpu["index"]): {
                "status": gpu["performance"]["status"],
                "supported_metrics": gpu["performance"]["supported_metrics"],
            }
            for gpu in samples[-1]["gpus"] if gpu.get("performance")
        },
        "sample_to_manager_ms": dist([
            (node["received_at"] - node["sampled_at"]) * 1000 for node in samples
        ]),
        "processes": usage,
    }


async def commit_observer(database, measurements, done):
    # Poll committed rows through a separate read-only connection.
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
        previous = 0.0
        while not done.is_set():
            row = connection.execute(
                "SELECT last_seen_at FROM nodes WHERE node_id = ?", (NODE_ID,)
            ).fetchone()
            if row and row[0] > previous:
                previous = row[0]
                measurements.append((time.time() - previous) * 1000)
            await asyncio.sleep(0.02)


async def benchmark(implementation, seconds, history_db=None, history_node=None):
    directory = ROOT / "run" / f"preview-benchmark-{implementation}-{time.time_ns()}"
    directory.mkdir(mode=0o700, parents=True)
    token_file = directory / "agent-token"
    token_file.write_text(secrets.token_urlsafe(32))
    token_file.chmod(0o600)
    env = {key: value for key, value in os.environ.items() if not key.startswith("CONSTELLA_")}
    env.update(
        CONSTELLA_AGENT_TOKEN_FILE=str(token_file),
        CONSTELLA_MANAGER_URL=f"ws://127.0.0.1:{PORT}/api/agents/ws",
        CONSTELLA_NODE_ID=NODE_ID,
        CONSTELLA_DEVICE_TYPE="nvidia",
        CONSTELLA_REFRESH_SECONDS="1",
        CONSTELLA_PROCESS_SECONDS="5",
        CONSTELLA_AGENT_STATE_FILE=str(directory / "agent-state.json"),
        CONSTELLA_BIND=f"127.0.0.1:{PORT}",
        CONSTELLA_DB_PATH=str(directory / "history.sqlite3"),
    )
    if history_db is not None:
        database = directory / "history.sqlite3"
        source_uri = f"{history_db.resolve().as_uri()}?mode=ro"
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as source,
            closing(sqlite3.connect(database)) as target,
        ):
            source.backup(target)
            if history_node is None:
                latest = target.execute("SELECT node_id FROM nodes ORDER BY last_seen_at DESC LIMIT 1").fetchone()
                if latest is None:
                    raise ValueError("The history database contains no nodes")
                history_node = latest[0]
    if implementation == "python":
        manager_args = [str(ROOT / ".venv/bin/constella"), "serve", "--host", "127.0.0.1", "--port", str(PORT)]
        agent_args = [str(ROOT / ".venv/bin/constella"), "agent"]
    else:
        manager_args = [os.environ.get("CONSTELLA_PREVIEW_MANAGER", str(ROOT / "target/release/constella-manager"))]
        agent_args = [os.environ.get("CONSTELLA_PREVIEW_AGENT", str(ROOT / "target/release/constella-agent"))]
    try:
        with urlopen(URL + "/api/health", timeout=1):
            raise RuntimeError(f"Port {PORT} is already in use")
    except OSError as error:
        if not isinstance(getattr(error, "reason", None), ConnectionRefusedError):
            raise
    with ExitStack() as stack:
        manager = launch(stack, directory, env, "manager", manager_args)
        await wait_ready(manager)
        if history_db is None:
            async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/cluster", max_size=8 * 1024 * 1024) as ws:
                agent = launch(stack, directory, env, "agent", agent_args)
                measurements = []
                done = asyncio.Event()
                async with asyncio.TaskGroup() as group:
                    group.create_task(commit_observer(directory / "history.sqlite3", measurements, done))
                    try:
                        results = await observe(ws, {"agent": agent, "manager": manager}, seconds)
                    finally:
                        done.set()
                results["sample_to_commit_ms_observed"] = dist(measurements[1:])
                results["commit_probe_resolution_ms"] = 20
            results["cluster_snapshot_http_ms"] = optional_query("/api/cluster/snapshot")
            results.update(query_timings(NODE_ID, "1h", "1h"))
        else:
            results = {"history_node": history_node, "database_bytes": database.stat().st_size}
            results.update(query_timings(history_node, "30d", "7d"))
        results["database"] = request("/api/health")["database"]
    results.update({"implementation": implementation, "preview_directory": str(directory.relative_to(ROOT))})
    report = json.dumps(results, indent=2)
    (directory / "results.json").write_text(report + "\n")
    print(report)


def query_timings(node, node_range, overview_range):
    encoded = quote(node, safe="")
    return {
        "gpu_history_http_ms": optional_query(f"/api/history/gpu?node_id={encoded}"),
        "node_analytics_http_ms": analytics_query(f"/api/analytics/node/{encoded}?range={node_range}"),
        "overview_analytics_http_ms": analytics_query(f"/api/analytics/overview?range={overview_range}"),
        "jobs_http_ms": optional_query(f"/api/highres/jobs?node_id={encoded}"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("implementation", choices=("python", "rust"))
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--history-db", type=Path, help="query an isolated copy of an existing SQLite history database")
    parser.add_argument("--history-node", help="select a node from the copied database")
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("--seconds must be positive")
    if args.history_node is not None and args.history_db is None:
        parser.error("--history-node requires --history-db")
    asyncio.run(benchmark(args.implementation, args.seconds, args.history_db, args.history_node))
