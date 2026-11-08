"""TTL-кэш метаданных/поиска в памяти."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Hashable


@dataclass
class _Entry:
    value: Any
    expires: float


class TTLCache:
    def __init__(self, ttl: float = 60.0, max_entries: int = 512):
        self.ttl = ttl
        self.max_entries = max_entries
        self._data: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: Hashable) -> Any | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expires < time.monotonic():
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return entry.value

    async def set(self, key: Hashable, value: Any, ttl: float | None = None) -> None:
        async with self._lock:
            self._data[key] = _Entry(value, time.monotonic() + (ttl or self.ttl))
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    async def delete(self, key: Hashable) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses,
                "max_entries": self.max_entries}


metadata_cache = TTLCache(ttl=300.0, max_entries=1024)
search_cache = TTLCache(ttl=45.0, max_entries=256)
cover_cache = TTLCache(ttl=3600.0, max_entries=128)


__all__ = ["TTLCache", "metadata_cache", "search_cache", "cover_cache"]
