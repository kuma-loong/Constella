"""Run bounded historical queries with an independent read-only SQLite connection."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .db import SQLiteStore


class HistoryReader:
    def __init__(self, path: Path):
        self.path = path
        self.workers = asyncio.Semaphore(2)

    async def query(self, function, **kwargs):
        async with self.workers:
            task = asyncio.create_task(asyncio.to_thread(self._query, function, kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Keep the concurrency slot until the SQLite worker has actually stopped.
                await task
                raise

    def _query(self, function, kwargs):
        store = SQLiteStore(self.path)
        store.open_readonly()
        deadline = time.monotonic() + 10.0
        try:
            store.connection.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
            return function(store, **kwargs)
        finally:
            store.close()
