"""Офлайн-события плеера (local-first §17) и пачка треков по id (§10):
идемпотентность по (user_id, event_id), ack без записи для неизвестных
треков/личных действий, shape /api/tracks/batch."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.models import Base as CoreBase, PlayEvent, Track
from server.auth import CurrentUser, tg_user
from server.routes import tracks


@asynccontextmanager
async def _fake_session():
    raise AssertionError("get_session не должен зваться без monkeypatch")
    yield None  # pragma: no cover


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(tracks.router)
    app.dependency_overrides[tg_user] = lambda: CurrentUser(id=1)
    return app


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.fixture
async def offline_db(db_engine, monkeypatch):
    async with db_engine.begin() as conn:
        await conn.run_sync(CoreBase.metadata.create_all)
    maker = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tracks.runtime, "get_session", maker)
    return maker


async def _seed_track(maker, title="Song") -> int:
    async with maker() as s:
        t = Track(source="yandex", external_id="y:1", title=title, artist="Artist")
        s.add(t)
        await s.commit()
        return t.id


class TestHistorySync:
    async def test_double_post_same_event_id_adds_one_row(self, client, offline_db):
        tid = await _seed_track(offline_db)
        ev = {"event_id": "e-abc-1", "track_id": tid, "kind": "finished",
              "played_seconds": 180.0, "finished": True, "at": 1737000000000}

        r1 = await client.post("/api/history/sync", json={"events": [ev]})
        r2 = await client.post("/api/history/sync", json={"events": [ev]})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["acked"] == ["e-abc-1"]
        assert r2.json()["acked"] == ["e-abc-1"]   # дедуп на сервере — тоже ack

        async with offline_db() as s:
            from sqlalchemy import select
            rows = list((await s.execute(select(PlayEvent))).scalars())
        assert len(rows) == 1
        assert rows[0].event_id == "e-abc-1"
        assert rows[0].user_id == 1
        assert rows[0].track_id == tid
        assert rows[0].origin == "web"
        assert rows[0].finished is True

    async def test_unknown_track_acked_without_row(self, client, offline_db):
        r = await client.post("/api/history/sync", json={"events": [
            {"event_id": "e-x", "track_id": 999999, "kind": "started"}]})
        assert r.status_code == 200
        assert r.json()["acked"] == ["e-x"]
        async with offline_db() as s:
            from sqlalchemy import select
            rows = list((await s.execute(select(PlayEvent))).scalars())
        assert rows == []

    async def test_personal_actions_acked_without_row(self, client, offline_db):
        tid = await _seed_track(offline_db)
        r = await client.post("/api/history/sync", json={"events": [
            {"event_id": "e-like", "track_id": tid, "kind": "liked"},
            {"event_id": "e-add", "track_id": tid, "kind": "added_to_playlist"},
        ]})
        assert r.json()["acked"] == ["e-like", "e-add"]
        async with offline_db() as s:
            from sqlalchemy import select
            rows = list((await s.execute(select(PlayEvent))).scalars())
        assert rows == []

    async def test_oversized_duration_acked_without_row(self, client, offline_db):
        tid = await _seed_track(offline_db)
        r = await client.post("/api/history/sync", json={"events": [
            {"event_id": "e-big", "track_id": tid, "kind": "finished",
             "played_seconds": 5 * 3600}]})
        assert r.json()["acked"] == ["e-big"]
        async with offline_db() as s:
            from sqlalchemy import select
            rows = list((await s.execute(select(PlayEvent))).scalars())
        assert rows == []

    async def test_too_many_events_rejected(self, client, offline_db):
        r = await client.post("/api/history/sync", json={"events": [
            {"event_id": f"e{i}", "kind": "started"} for i in range(201)]})
        assert r.status_code == 400

    async def test_missing_started_at_falls_back_to_now(self, client, offline_db):
        tid = await _seed_track(offline_db)
        r = await client.post("/api/history/sync", json={"events": [
            {"event_id": "e-noat", "track_id": tid, "kind": "started"}]})
        assert r.status_code == 200
        async with offline_db() as s:
            from sqlalchemy import select
            row = (await s.execute(select(PlayEvent))).scalars().one()
        assert row.started_at is not None      # дефолт utcnow сработал


class TestTracksBatch:
    async def test_batch_returns_tracks_in_request_order(self, client, offline_db):
        async with offline_db() as s:
            a = Track(source="yandex", external_id="y:1", title="A", artist="B")
            b = Track(source="zaycev", external_id="z:1", title="C", artist="D")
            s.add_all([a, b])
            await s.commit()
            ids = [a.id, b.id]

        r = await client.post("/api/tracks/batch", json={"ids": ids + [999999]})
        assert r.status_code == 200
        data = r.json()
        assert [t["id"] for t in data["tracks"]] == ids   # пропавший id просто нет
        assert data["tracks"][0]["title"] == "A"

    async def test_batch_empty_and_dedup(self, client, offline_db):
        tid = await _seed_track(offline_db)
        r = await client.post("/api/tracks/batch", json={"ids": []})
        assert r.json() == {"tracks": []}
        r = await client.post("/api/tracks/batch", json={"ids": [tid, tid]})
        assert len(r.json()["tracks"]) == 1
