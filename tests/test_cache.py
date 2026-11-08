"""TTL-кэш и файловый кэш треков (SQLite in-memory)."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from music.cache.metadata_cache import TTLCache
from music.cache.music_cache import (cache_path, cleanup_cache, drop_cache_row,
                                     lookup_cached, set_telegram_file,
                                     store_cached)
from music.config import music_settings
from music.models import MusicCachedFile
from music.storage import sha256_file


class TestTTLCache:
    async def test_set_and_get(self):
        cache = TTLCache(ttl=60)
        await cache.set("key", {"value": 1})
        assert await cache.get("key") == {"value": 1}
        assert cache.hits == 1

    async def test_missing(self):
        cache = TTLCache()
        assert await cache.get("nope") is None
        assert cache.misses == 1

    async def test_expires(self):
        cache = TTLCache(ttl=0.01)
        await cache.set("key", "value")
        await asyncio.sleep(0.05)
        assert await cache.get("key") is None

    async def test_custom_ttl(self):
        cache = TTLCache(ttl=60)
        await cache.set("key", "value", ttl=0.01)
        await asyncio.sleep(0.05)
        assert await cache.get("key") is None

    async def test_eviction(self):
        cache = TTLCache(ttl=60, max_entries=2)
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.set("c", 3)
        assert await cache.get("a") is None
        assert await cache.get("c") == 3

    async def test_delete_and_clear(self):
        cache = TTLCache()
        await cache.set("a", 1)
        await cache.delete("a")
        assert await cache.get("a") is None
        await cache.set("b", 2)
        await cache.clear()
        assert cache.stats()["entries"] == 0


class TestMusicCache:
    def test_cache_path(self, temp_music_dir: Path):
        path = cache_path("ab" * 32, ".mp3")
        assert path.parent == music_settings.cache_dir / "ab"
        assert path.suffix == ".mp3"

    async def test_store_and_lookup(self, db_session, temp_music_dir: Path,
                                    audio_files):
        source = temp_music_dir / "source.mp3"
        shutil.copy2(audio_files["mp3"], source)
        sha = sha256_file(source)
        row = await store_cached(db_session, source, sha256=sha, file_format="mp3",
                                 duration=12, identity_key="artist|song|original|0",
                                 cache_key="key-1", quality_category="HIGH_QUALITY",
                                 bitrate_kbps=320, sample_rate=44100,
                                 title="Song", artist="Artist")
        assert row.cache_key == "key-1"
        assert Path(row.path).exists()
        assert not source.exists()  # move=True

        found = await lookup_cached(db_session, cache_key="key-1")
        assert found is not None
        assert found.title == "Song"

        by_sha = await lookup_cached(db_session, sha256=sha)
        assert by_sha is not None

        by_identity = await lookup_cached(db_session, identity_key="artist|song|original|0")
        assert by_identity is not None

    async def test_lookup_missing_path_removes_row(self, db_session,
                                                   temp_music_dir: Path):
        source = temp_music_dir / "gone.mp3"
        source.write_bytes(b"ID3data")
        row = await store_cached(db_session, source, sha256=sha256_file(source),
                                 file_format="mp3", cache_key="key-2")
        Path(row.path).unlink()
        assert await lookup_cached(db_session, cache_key="key-2") is None
        assert await lookup_cached(db_session, cache_key="key-2") is None

    async def test_expired_row_hidden(self, db_session, temp_music_dir: Path):
        source = temp_music_dir / "exp.mp3"
        source.write_bytes(b"ID3data")
        row = await store_cached(db_session, source, sha256=sha256_file(source),
                                 file_format="mp3", cache_key="key-3")
        from music.models import utcnow
        from datetime import timedelta
        row.expires_at = utcnow() - timedelta(days=1)
        await db_session.commit()
        assert await lookup_cached(db_session, cache_key="key-3") is None
        assert await lookup_cached(db_session, cache_key="key-3",
                                   allow_expired=True) is not None

    async def test_set_telegram_file(self, db_session, temp_music_dir: Path):
        source = temp_music_dir / "tg.mp3"
        source.write_bytes(b"ID3data")
        await store_cached(db_session, source, sha256=sha256_file(source),
                           file_format="mp3", cache_key="key-4")
        await set_telegram_file(db_session, "key-4", "FILEID", "UNIQUE")
        row = await lookup_cached(db_session, cache_key="key-4")
        assert row.telegram_file_id == "FILEID"
        assert row.telegram_file_unique_id == "UNIQUE"

    async def test_cleanup_cache_removes_over_limit(self, db_session,
                                                    temp_music_dir: Path):
        source = temp_music_dir / "big.mp3"
        source.write_bytes(b"ID3" + b"x" * 4096)
        row = await store_cached(db_session, source, sha256=sha256_file(source),
                                 file_format="mp3", cache_key="key-5")
        path = Path(row.path)
        result = await cleanup_cache(db_session, max_mb=0)
        assert result["rows"] >= 1
        assert not path.exists()

    async def test_drop_cache_row(self, db_session, temp_music_dir: Path):
        source = temp_music_dir / "drop.mp3"
        source.write_bytes(b"ID3data")
        await store_cached(db_session, source, sha256=sha256_file(source),
                           file_format="mp3", cache_key="key-6")
        await drop_cache_row(db_session, "key-6")
        assert await lookup_cached(db_session, cache_key="key-6") is None

    async def test_lookup_without_key_returns_none(self, db_session):
        assert await lookup_cached(db_session) is None
