"""Bounded JSON results with fixed TTL and one in-flight computation per key."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Any

CACHE_SECONDS = 60
MAX_CACHE_BYTES = 32 * 1024 * 1024


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(slots=True)
class Entry:
    body: bytes
    expires_at: float


class ResultCache:
    def __init__(self, *, ttl: float = CACHE_SECONDS, max_bytes: int = MAX_CACHE_BYTES,
                 max_entries: int = 128, max_pending: int = 16):
        self.ttl = ttl
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.max_pending = max_pending
        self.entries: OrderedDict[Hashable, Entry] = OrderedDict()
        self.pending: dict[Hashable, asyncio.Task[bytes]] = {}
        self.size = 0

    async def get(self, key: Hashable, loader: Callable[[], Awaitable[Any]]) -> bytes:
        now = time.monotonic()
        entry = self.entries.get(key)
        if entry is not None:
            if now < entry.expires_at:
                self.entries.move_to_end(key)
                return entry.body
            self._remove(key)
        task = self.pending.get(key)
        if task is None:
            if len(self.pending) >= self.max_pending:
                raise CacheBusyError
            task = asyncio.create_task(self._load(key, loader))
            self.pending[key] = task
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    async def _load(self, key: Hashable, loader: Callable[[], Awaitable[Any]]) -> bytes:
        try:
            value = await loader()
            body = await asyncio.to_thread(json_bytes, value)
            now = time.monotonic()
            for old_key, entry in list(self.entries.items()):
                if now >= entry.expires_at:
                    self._remove(old_key)
            if len(body) <= self.max_bytes:
                while self.entries and (
                    self.size + len(body) > self.max_bytes or len(self.entries) >= self.max_entries
                ):
                    self._remove(next(iter(self.entries)))
                self.entries[key] = Entry(body, now + self.ttl)
                self.size += len(body)
            return body
        finally:
            self.pending.pop(key, None)

    def _remove(self, key: Hashable) -> None:
        self.size -= len(self.entries.pop(key).body)


class CacheBusyError(Exception):
    """The bounded queue already contains too many distinct cache misses."""
