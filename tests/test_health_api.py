"""Health-check и HTTP API музыкальной подсистемы."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from music import api
from music import health


@pytest.fixture
async def client(db_maker, monkeypatch):
    monkeypatch.setattr(api, "AsyncSessionLocal", db_maker)
    app = FastAPI()
    app.include_router(api.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as http:
        yield http


class TestCollectHealth:
    async def test_ok(self, monkeypatch):
        async def db_ok():
            return True

        monkeypatch.setattr(health, "db_healthy", db_ok)
        monkeypatch.setattr(health, "_tool_ok", lambda path: True)
        monkeypatch.setattr(health, "_disk_free_mb", lambda: 12345)
        monkeypatch.setattr(health.registry, "snapshot",
                            lambda: {"jamendo": {"health": "ok"}})
        monkeypatch.setattr(health.queue, "stats",
                            lambda: {"queued": 1, "active": 0})
        report = await health.collect_health()
        assert report.ok is True
        assert report.database == "ok"
        assert report.ffmpeg == "ok"
        assert report.disk_free_mb == 12345
        assert report.queue["queued"] == 1
        assert report.providers["jamendo"]["health"] == "ok"
        assert report.checked_at > 0

    async def test_database_down(self, monkeypatch):
        async def db_down():
            return False

        monkeypatch.setattr(health, "db_healthy", db_down)
        monkeypatch.setattr(health, "_tool_ok", lambda path: True)
        monkeypatch.setattr(health.registry, "snapshot", lambda: {})
        monkeypatch.setattr(health.queue, "stats", lambda: {})
        report = await health.collect_health()
        assert report.ok is False
        assert report.database == "down"

    async def test_ffmpeg_missing(self, monkeypatch):
        async def db_ok():
            return True

        monkeypatch.setattr(health, "db_healthy", db_ok)
        monkeypatch.setattr(health, "_tool_ok", lambda path: False)
        monkeypatch.setattr(health.registry, "snapshot", lambda: {})
        monkeypatch.setattr(health.queue, "stats", lambda: {})
        report = await health.collect_health()
        assert report.ok is False
        assert report.ffmpeg == "missing"

    async def test_deep_check_uses_provider_health(self, monkeypatch):
        async def db_ok():
            return True

        async def health_check(timeout: float = 8.0):
            return {"jamendo": {"health": "ok"}, "spotify": {"health": "unconfigured"}}

        monkeypatch.setattr(health, "db_healthy", db_ok)
        monkeypatch.setattr(health, "_tool_ok", lambda path: True)
        monkeypatch.setattr(health.registry, "health_check", health_check)
        monkeypatch.setattr(health.queue, "stats", lambda: {})
        report = await health.collect_health(deep=True)
        assert report.providers["jamendo"]["health"] == "ok"
        assert report.providers["spotify"]["health"] == "unconfigured"

    async def test_deep_check_survives_provider_error(self, monkeypatch):
        async def db_ok():
            return True

        async def boom(timeout: float = 8.0):
            raise RuntimeError("network down")

        monkeypatch.setattr(health, "db_healthy", db_ok)
        monkeypatch.setattr(health, "_tool_ok", lambda path: True)
        monkeypatch.setattr(health.registry, "health_check", boom)
        monkeypatch.setattr(health.registry, "snapshot",
                            lambda: {"jamendo": {"health": "unknown"}})
        monkeypatch.setattr(health.queue, "stats", lambda: {})
        report = await health.collect_health(deep=True)
        assert report.providers["jamendo"]["health"] == "unknown"

    async def test_health_dict(self, monkeypatch):
        async def db_ok():
            return True

        monkeypatch.setattr(health, "db_healthy", db_ok)
        monkeypatch.setattr(health, "_tool_ok", lambda path: True)
        monkeypatch.setattr(health, "_disk_free_mb", lambda: 1)
        monkeypatch.setattr(health.registry, "snapshot", lambda: {})
        monkeypatch.setattr(health.queue, "stats", lambda: {})
        data = await health.health_dict()
        assert data["ok"] is True
        assert "providers" in data
        assert "queue" in data


class TestApi:
    async def test_health_endpoint(self, client, monkeypatch):
        async def fake_health(deep: bool = False):
            return {"ok": True, "database": "ok", "deep": deep}

        monkeypatch.setattr(api, "health_dict", fake_health)
        response = await client.get("/api/music/health")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["version"] == "1.0.0"

    async def test_health_endpoint_deep(self, client, monkeypatch):
        seen = {}

        async def fake_health(deep: bool = False):
            seen["deep"] = deep
            return {"ok": True}

        monkeypatch.setattr(api, "health_dict", fake_health)
        response = await client.get("/api/music/health?deep=true")
        assert response.status_code == 200
        assert seen["deep"] is True

    async def test_sources_endpoint(self, client, monkeypatch):
        monkeypatch.setattr(api, "registry", SimpleNamespace(snapshot=lambda: {
            "jamendo": {
                "display_name": "Jamendo", "enabled": True, "configured": True,
                "health": "ok", "capabilities": {"download": True},
            },
            "spotify": {
                "display_name": "Spotify", "enabled": False, "configured": False,
                "health": "unconfigured", "capabilities": {"download": False},
            },
        }))
        response = await client.get("/api/music/sources")
        assert response.status_code == 200
        sources = response.json()["sources"]
        assert len(sources) == 2
        by_name = {item["provider"]: item for item in sources}
        assert by_name["jamendo"]["enabled"] is True
        assert by_name["jamendo"]["capabilities"]["download"] is True
        assert by_name["spotify"]["health"] == "unconfigured"

    async def test_stats_endpoint_empty(self, client):
        response = await client.get("/api/music/stats")
        assert response.status_code == 200
        assert response.json() == {
            "tracks": 0, "downloads": 0, "cached_files": 0, "cached_bytes": 0}

    async def test_stats_endpoint_with_rows(self, client, db_maker):
        from music.models import MusicCachedFile, MusicDownloadHistory, MusicTrack
        async with db_maker() as session:
            session.add(MusicTrack(provider="jamendo", external_id="1",
                                   title="Song", identity_key="artist|song|original|0"))
            session.add(MusicCachedFile(cache_key="key", path="C:/cache/x.mp3",
                                        size=1234))
            session.add(MusicDownloadHistory(user_id=1))
            await session.commit()
        response = await client.get("/api/music/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["tracks"] == 1
        assert data["downloads"] == 1
        assert data["cached_files"] == 1
        assert data["cached_bytes"] == 1234
