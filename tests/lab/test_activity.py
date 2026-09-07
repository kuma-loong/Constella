from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from constella.cluster import ClusterState
from constella.db import SQLiteStore
from constella_lab import activity
from constella_lab.activity import (
    ActivityData,
    ActivityService,
    current_status,
    payload,
    read_activity,
)
from constella_lab.activity_usage import TIMEZONE, resolve_usage, summary, user_statistics

HOUR = 3600
START = datetime(2026, 9, 1, tzinfo=TIMEZONE).timestamp()


def row(
    node="a", uid=1001, user="usra", card="0", start=START, end=START + 2 * HOUR, pid=1, **extra
):
    return {
        "node_id": node,
        "user_uid": uid,
        "user": user,
        "gpu_uuid": card,
        "gpu_name": "NVIDIA H100 80GB HBM3",
        "device_type": "nvidia",
        "card_id": None,
        "session_id": f"{node}:{pid}",
        "session_first_seen_at": start,
        "session_last_seen_at": end,
        "first_seen_at": start,
        "last_seen_at": end,
        "pid": pid,
        "ppid": None,
        "process_start_time": start,
        "parent_start_time": None,
        "task_name": f"train-{pid}",
        "status": "ended",
        **extra,
    }


def binding(node="a", uid=1001, owner="kuma", start=START, end=None):
    return {
        "node_id": node,
        "unix_uid": uid,
        "user_id": owner,
        "user_display_name": owner,
        "valid_from": start,
        "valid_to": end,
    }


def test_bound_nodes_union_physical_cards_and_unbound_names_stay_separate():
    rows = [
        row(card="0"),
        row(card="1"),
        row(card="0", pid=2),
        row(node="b", uid=1002, user="usrb"),
        row(node="c"),
        row(node="d"),
    ]
    stats = user_statistics(rows, [binding(), binding("b", 1002)], START, START + 3 * HOUR)
    kuma = next(s for s in stats if s["owner_key"] == "user:kuma")
    assert (kuma["gpu_hours"], kuma["active_hours"], kuma["active_days"]) == (6, 2, 1)
    assert len(stats) == 3
    assert {s["user"] for s in stats} == {"kuma", "usra @ c", "usra @ d"}
    assert kuma["job_count"] == 3


def test_binding_boundaries_reassignment_missing_uid_and_renamed_account():
    rows = [row(end=START + 4 * HOUR)]
    bindings = [
        binding(start=START + HOUR, end=START + 2 * HOUR),
        binding(owner="new", start=START + 3 * HOUR),
    ]
    pieces = resolve_usage(rows, bindings, START, START + 5 * HOUR)
    assert [(r.owner, (r.end - r.start) / HOUR) for r in pieces] == [
        ('account:["a", 1001]', 1),
        ("user:kuma", 1),
        ('account:["a", 1001]', 1),
        ("user:new", 1),
    ]
    assert all(
        not r.owner.startswith("user:")
        for r in resolve_usage([row(uid=None)], bindings, START, START + 4 * HOUR)
    )
    renamed = user_statistics([row(), row(user="renamed", pid=2)], [], START, START + 3 * HOUR)
    assert len(renamed) == 1 and renamed[0]["gpu_hours"] == 2


def test_midnight_zero_length_gpu_changes_and_ascend_dies():
    rows = [
        row(start=START - HOUR, end=START + HOUR),
        row(card="1", start=START + HOUR, end=START + 2 * HOUR),
        row(card="2", start=START + 24 * HOUR, end=START + 24 * HOUR),
    ]
    records = resolve_usage(rows, [], START - 2 * HOUR, START + 48 * HOUR)
    assert summary(records, START - 2 * HOUR, START + 48 * HOUR) == {
        "gpu_hours": 3,
        "active_hours": 3,
        "active_days": 3,
    }
    assert summary(records, START, START + 24 * HOUR)["gpu_hours"] == 2
    midnight_end = resolve_usage(
        [row(start=START - HOUR, end=START)], [], START - HOUR, START + HOUR
    )
    assert summary(midnight_end, START, START + HOUR)["active_days"] == 0
    dies = resolve_usage(
        [
            row(card="die0", device_type="ascend", card_id="0"),
            row(card="die1", device_type="ascend", card_id="0"),
        ],
        [],
        START,
        START + 3 * HOUR,
    )
    assert summary(dies, START, START + 3 * HOUR)["gpu_hours"] == 2


def test_aggregation_precedes_top_twenty():
    rows = [row(node=f"n{i}", end=START + HOUR) for i in range(30)]
    bindings = [binding(f"n{i}") for i in range(25)]
    stats = user_statistics(rows, bindings, START, START + 2 * HOUR)
    assert stats[0]["owner_key"] == "user:kuma" and stats[0]["gpu_hours"] == 25


def test_pagination_totals_filters_and_expired_cursor():
    rows = [row(pid=i) for i in range(51)]
    data = ActivityData(
        resolve_usage(rows, [binding()], START, START + 3 * HOUR),
        START + 3 * HOUR,
        START,
        [("2026-09-01", START, START + 3 * HOUR)],
        [(START, START + 3 * HOUR)],
        0,
        100,
    )
    cluster = ClusterState(local_node_id="test")
    options = dict(date=None, model="", status="all", q="", cursor=None, limit=20)
    first = payload(data, cluster, **options)
    second = payload(data, cluster, **{**options, "cursor": first["jobs"]["next_cursor"]})
    assert (
        first["summary"]
        == second["summary"]
        == {"gpu_hours": 2, "active_hours": 2, "active_days": 1}
    )
    assert first["jobs"]["total"] == 51
    assert not {j["job_key"] for j in first["jobs"]["items"]} & {
        j["job_key"] for j in second["jobs"]["items"]
    }
    with pytest.raises(HTTPException) as stale:
        payload(data, cluster, **{**options, "q": "train", "cursor": first["jobs"]["next_cursor"]})
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as bad:
        payload(data, cluster, **{**options, "cursor": "invalid"})
    assert bad.value.status_code == 422
    assert payload(data, cluster, **{**options, "model": "A100"})["selection"]["gpu_hours"] == 0


def test_offline_and_failed_snapshot_never_claim_job_ended():
    job = {
        "node_id": "a",
        "status": "running",
        "sessions": [{"pid": 1, "process_start_time": START}],
    }
    cluster = SimpleNamespace(latest_by_node={})
    assert current_status(job, cluster, START) == "unknown"
    snap = SimpleNamespace(
        status="online", sampled_at=START, process_interval=5, error=None, gpus=[]
    )
    runtime = SimpleNamespace(connected=False, snapshot=snap)
    cluster.latest_by_node["a"] = runtime
    assert current_status(job, cluster, START) == "unknown"
    runtime.connected = True
    snap.error = "failed"
    assert current_status(job, cluster, START) == "unknown"
    snap.error = None
    snap.gpus = [SimpleNamespace(processes=[], other_users=[], error=None)]
    assert current_status(job, cluster, START) == "ended"


def test_busiest_window_can_cross_midnight():
    rows = [row(start=START + 23 * HOUR, end=START + 26 * HOUR)]
    data = ActivityData(
        resolve_usage(rows, [binding()], START, START + 48 * HOUR),
        START + 48 * HOUR,
        START,
        [
            ("2026-09-01", START, START + 24 * HOUR),
            ("2026-09-02", START + 24 * HOUR, START + 48 * HOUR),
        ],
        [(START, START + 48 * HOUR)],
        0,
        0,
    )
    assert activity.busiest_hours(data, data.records) == {"start_hour": 23, "end_hour": 2}


def test_readonly_empty_query_does_not_initialize_database(tmp_path, monkeypatch):
    path = tmp_path / "history.sqlite3"
    store = SQLiteStore(path)
    store.open()
    before = store.connection.execute("PRAGMA schema_version").fetchone()[0]
    monkeypatch.setattr(activity.time, "time", lambda: START + 3 * HOUR)
    data = read_activity(path, [binding()], "kuma", 7)
    assert data.records == [] and len(data.days) == 7
    assert store.connection.execute("PRAGMA schema_version").fetchone()[0] == before
    store.close()
    missing = tmp_path / "missing.sqlite3"
    import sqlite3

    with pytest.raises(sqlite3.OperationalError):
        read_activity(missing, [binding()], "kuma", 7)
    assert not missing.exists()


def test_cache_singleflight_identity_isolation_binding_invalidation(tmp_path, monkeypatch):
    calls = []

    def read(path, bindings, user, period):
        calls.append((user, period, bindings))
        return ActivityData([], activity.time.time(), 0, [], [], 0, 32)

    monkeypatch.setattr(activity, "read_activity", read)

    async def run():
        service = ActivityService()
        first, second = await asyncio.gather(
            *(service.get(tmp_path, [binding()], "kuma", 7) for _ in range(2))
        )
        assert first is second and len(calls) == 1
        await service.get(tmp_path, [binding()], "kuma", 7)
        assert len(calls) == 1
        await service.get(tmp_path, [binding()], "other", 7)
        await service.get(tmp_path, [binding(end=START + HOUR)], "kuma", 7)
        assert len(calls) == 3
        for i in range(20):
            await service.get(tmp_path, [], f"u{i}", 7)
        assert len(service.cache) == 16

    asyncio.run(run())


def test_legacy_username_matches_nodes_and_preserves_uid_priority():
    bindings = [
        {**binding(start=START + HOUR), "unix_username": "usra"},
        {**binding("b", 1002, start=START + HOUR), "unix_username": "usrb"},
    ]
    rows = [
        row(uid=None), row(node="b", uid=None, user="usrb"),
        row(node="c", uid=None), row(uid=9999), row(uid=None, user=None),
        row(uid=1001, user="renamed", start=START + HOUR),
    ]
    records = resolve_usage(rows, bindings, START, START + 3 * HOUR)
    owned = [r for r in records if r.owner == "user:kuma"]
    assert summary(owned, START, START + 3 * HOUR)["gpu_hours"] == 4
    assert len(owned) == 3
    assert len([r for r in records if r.owner.startswith("account:")]) == 3


def test_legacy_username_transfer_keeps_old_owner_and_unbound_gap():
    bindings = [
        {**binding(start=START + HOUR, end=START + 2 * HOUR), "unix_username": "usra"},
        {**binding(uid=2001, owner="new", start=START + 3 * HOUR), "unix_username": "usra"},
    ]
    records = resolve_usage([row(uid=None, end=START + 4 * HOUR)], bindings, START, START + 4 * HOUR)
    assert [(r.owner, (r.end - r.start) / HOUR) for r in records] == [
        ("user:kuma", 2), ('account:["a", "usra"]', 1), ("user:new", 1),
    ]
