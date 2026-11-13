"""init_db на «старой» схеме: мини-миграции добавляют page_url/license и
PG-совместимые BOOLEAN-дефолты (context_enabled, explicit)."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import core.db as cdb
from core.db import init_db


LEGACY_TRACKS = (
    "CREATE TABLE tracks ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " source VARCHAR(24) NOT NULL,"
    " external_id VARCHAR(128) NOT NULL,"
    " title VARCHAR(256) NOT NULL,"
    " artist VARCHAR(256),"
    " album VARCHAR(256), year INTEGER, isrc VARCHAR(16),"
    " duration INTEGER, explicit BOOLEAN NOT NULL DEFAULT 0,"
    " cover_url TEXT, quality VARCHAR(12), size_bytes BIGINT,"
    " file_id VARCHAR(256), local_path TEXT, downloads INTEGER,"
    " created_at DATETIME)")

LEGACY_WAVE_SETTINGS = (
    "CREATE TABLE wave_settings ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " user_id INTEGER, engine VARCHAR(16), mood VARCHAR(32),"
    " diversity VARCHAR(16), language VARCHAR(8),"
    " activities TEXT, genres TEXT, artists TEXT,"
    " explicit_ok BOOLEAN)"
    # context_enabled намеренно отсутствует — её добавит миграция
)


@pytest.fixture
async def legacy_db(tmp_path, monkeypatch):
    """База со «старой» схемой + подмена движка core.db на неё."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(LEGACY_TRACKS))
        await conn.execute(text(LEGACY_WAVE_SETTINGS))
    monkeypatch.setattr(cdb, "engine", engine)
    yield engine
    await engine.dispose()


async def _cols(engine, table: str) -> dict[str, str]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
    return {r[1]: (r[4] or "") for r in rows}   # name → dflt_value


class TestInitDbMigrations:
    async def test_track_new_columns_added(self, legacy_db):
        await init_db()
        cols = await _cols(legacy_db, "tracks")
        assert "page_url" in cols
        assert "license" in cols

    async def test_existing_data_kept(self, legacy_db):
        async with legacy_db.begin() as conn:
            await conn.execute(text(
                "INSERT INTO tracks (source, external_id, title, artist, duration)"
                " VALUES ('zaycev', 'old1', 'Старый трек', 'Старый артист', 200)"))
        await init_db()
        async with legacy_db.connect() as conn:
            row = (await conn.execute(text(
                "SELECT title, artist FROM tracks WHERE external_id='old1'"))).one()
        assert row == ("Старый трек", "Старый артист")

    async def test_context_enabled_added_with_default(self, legacy_db):
        """BOOLEAN-дефолт миграции (SQLite: 1)."""
        await init_db()
        cols = await _cols(legacy_db, "wave_settings")
        assert "context_enabled" in cols
        assert cols["context_enabled"] in ("1", "TRUE", "true", "'1'")

    async def test_explicit_column_untouched(self, legacy_db):
        await init_db()
        cols = await _cols(legacy_db, "tracks")
        assert "explicit" in cols                      # не сломали существующую

    async def test_tracks_insertable_with_new_columns(self, legacy_db):
        await init_db()
        async with legacy_db.begin() as conn:
            await conn.execute(text(
                "INSERT INTO tracks (source, external_id, title, artist, duration,"
                " page_url, license) VALUES ('vk', 'search:кино',"
                " 'VK Музыка: официальный поиск «кино»', '', 0,"
                " 'https://vk.com/audio?q=x', NULL)"))
            row = (await conn.execute(text(
                "SELECT page_url FROM tracks WHERE source='vk'"))).one()
        assert row[0] == "https://vk.com/audio?q=x"
