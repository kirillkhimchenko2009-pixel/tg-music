"""Mini App: /api/download и /api/jobs — ownership, dedupe, limits, delivery."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from server.auth import CurrentUser, tg_user
from server.routes import tracks


@asynccontextmanager
async def _fake_session():
    yield SimpleNamespace()


def _fake_track(track_id=5, *, file_id=None, local_path=None):
    return SimpleNamespace(
        id=track_id, title="Song", artist="Artist", file_id=file_id,
        duration=12, cover_url=None, local_path=local_path,
        quality="aac320")


def _job(track_id=5, user_id=1, quality="aac320", job_id="job-1"):
    now = tracks._now()
    tracks.JOB_STATUS[job_id] = {
        "status": "queued", "progress": 0.0, "user": user_id,
        "created_at": now, "updated_at": now}
    tracks._INFLIGHT[(int(user_id), int(track_id), quality)] = job_id
    return job_id


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(tracks.router)
    app.dependency_overrides[tg_user] = lambda: CurrentUser(id=1)
    return app


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as http:
        yield http


@pytest.fixture(autouse=True)
async def _clean_jobs():
    tracks.JOB_STATUS.clear()
    tracks._INFLIGHT.clear()
    tracks._ACTIVE_TASKS.clear()
    yield
    tasks = list(tracks._ACTIVE_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    tracks.JOB_STATUS.clear()
    tracks._INFLIGHT.clear()
    tracks._ACTIVE_TASKS.clear()


@pytest.fixture
def pending_delivery(monkeypatch):
    gate = asyncio.Event()

    async def fake_deliver(job_id, user_id, track_id, quality):
        try:
            await gate.wait()
            status = tracks.JOB_STATUS.get(job_id)
            if status:
                tracks._touch(status, status="done", progress=100.0)
        finally:
            tracks._INFLIGHT.pop((int(user_id), int(track_id), quality), None)

    monkeypatch.setattr(tracks, "_deliver_job", fake_deliver)
    return gate


async def _finish_tasks():
    tasks = list(tracks._ACTIVE_TASKS)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class TestJobOwnership:
    async def test_owner_sees_status_without_service_fields(self, client):
        _job(job_id="own-job")
        response = await client.get("/api/jobs/own-job")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["progress"] == 0.0
        assert "user" not in data
        assert "created_at" not in data
        assert "updated_at" not in data

    async def test_other_user_gets_404(self, client, app):
        _job(job_id="private-job")
        app.dependency_overrides[tg_user] = lambda: CurrentUser(id=2)
        response = await client.get("/api/jobs/private-job")
        assert response.status_code == 404
        assert "user" not in response.json()

    async def test_unknown_job_is_404(self, client):
        response = await client.get("/api/jobs/missing")
        assert response.status_code == 404


class TestJobsPruning:
    async def test_terminal_jobs_expire_but_active_jobs_do_not(self, client,
                                                               monkeypatch):
        monkeypatch.setattr(tracks, "_JOB_TTL_SEC", 10.0)
        now = tracks._now()
        tracks.JOB_STATUS["old-done"] = {
            "status": "done", "progress": 100.0, "user": 1,
            "created_at": now - 100, "updated_at": now - 100}
        tracks.JOB_STATUS["old-active"] = {
            "status": "downloading", "progress": 10.0, "user": 1,
            "created_at": now - 100, "updated_at": now - 100}

        assert (await client.get("/api/jobs/old-done")).status_code == 404
        response = await client.get("/api/jobs/old-active")
        assert response.status_code == 200
        assert response.json()["status"] == "downloading"

    async def test_cache_size_is_capped(self, client, monkeypatch):
        monkeypatch.setattr(tracks, "_JOB_LIMIT", 2)
        now = tracks._now()
        for index in range(5):
            tracks.JOB_STATUS[f"job-{index}"] = {
                "status": "done", "progress": 100.0, "user": 1,
                "created_at": now + index, "updated_at": now + index}

        await client.get("/api/jobs/does-not-exist")

        assert len(tracks.JOB_STATUS) <= 2
        assert "job-0" not in tracks.JOB_STATUS
        assert "job-4" in tracks.JOB_STATUS


class TestGuestDownload:
    async def test_guest_is_rejected_before_job_creation(self):
        app = FastAPI()
        app.include_router(tracks.router)
        app.dependency_overrides[tg_user] = lambda: CurrentUser(
            id=1_000_000_000_123)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as http:
            response = await http.post("/api/download?track_id=5")

        assert response.status_code == 409
        assert response.json()["detail"] == "telegram_required"
        assert tracks.JOB_STATUS == {}
        assert tracks._INFLIGHT == {}


class TestDownloadDedupe:
    async def test_double_click_returns_the_same_job(self, client,
                                                     pending_delivery):
        first = await client.post("/api/download?track_id=5")
        second = await client.post("/api/download?track_id=5")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["job"] == second.json()["job"]
        assert second.json().get("deduplicated") is True

        pending_delivery.set()
        await _finish_tasks()

    async def test_new_job_after_previous_finished(self, client,
                                                   pending_delivery):
        first = await client.post("/api/download?track_id=5")
        pending_delivery.set()
        await _finish_tasks()

        second = await client.post("/api/download?track_id=5")
        assert second.json()["job"] != first.json()["job"]
        await _finish_tasks()

    async def test_different_quality_is_not_deduplicated(self, client,
                                                         pending_delivery):
        first = await client.post("/api/download?track_id=5&quality=aac320")
        second = await client.post("/api/download?track_id=5&quality=flac")
        assert first.json()["job"] != second.json()["job"]
        pending_delivery.set()
        await _finish_tasks()

    async def test_other_user_gets_own_job(self, client, app, pending_delivery):
        first = await client.post("/api/download?track_id=5")
        app.dependency_overrides[tg_user] = lambda: CurrentUser(id=2)
        second = await client.post("/api/download?track_id=5")
        assert first.json()["job"] != second.json()["job"]
        pending_delivery.set()
        await _finish_tasks()


class TestActiveLimit:
    async def test_too_many_parallel_downloads_returns_429(
            self, client, monkeypatch, pending_delivery):
        monkeypatch.setattr(tracks, "_MAX_ACTIVE_DOWNLOADS", 2)
        assert (await client.post("/api/download?track_id=1")).status_code == 200
        assert (await client.post("/api/download?track_id=2")).status_code == 200
        response = await client.post("/api/download?track_id=3")

        assert response.status_code == 429
        assert "одновременных" in response.json()["detail"]
        pending_delivery.set()
        await _finish_tasks()

    async def test_terminal_jobs_do_not_count_towards_limit(
            self, client, monkeypatch):
        monkeypatch.setattr(tracks, "_MAX_ACTIVE_DOWNLOADS", 1)
        now = tracks._now()
        tracks.JOB_STATUS["done-job"] = {
            "status": "done", "progress": 100.0, "user": 1,
            "created_at": now, "updated_at": now}

        async def instant_deliver(job_id, user_id, track_id, quality):
            status = tracks.JOB_STATUS.get(job_id)
            if status:
                tracks._touch(status, status="done", progress=100.0)
            tracks._INFLIGHT.pop((int(user_id), int(track_id), quality), None)

        monkeypatch.setattr(tracks, "_deliver_job", instant_deliver)
        response = await client.post("/api/download?track_id=9")
        assert response.status_code == 200
        await _finish_tasks()


class TestDeliverJob:
    def _install(self, monkeypatch, track, *, send_result="FID",
                 preview_result=False, cached_result=False):
        calls = SimpleNamespace(send=[], save=[], preview=[], cached=[])
        monkeypatch.setattr(tracks.runtime, "get_session", _fake_session)

        async def get_track(session, track_id):
            return track

        monkeypatch.setattr(tracks.catalog, "get_track", get_track)
        monkeypatch.setattr(tracks.catalog, "meta_of", lambda value: SimpleNamespace())

        async def fake_bot():
            return SimpleNamespace()

        monkeypatch.setattr(tracks, "_bot", fake_bot)

        async def send_audio_file(bot, chat_id, value, path):
            calls.send.append((value.id, path))
            return send_result

        async def save_file_id(track_id, file_id, quality=None, **kwargs):
            calls.save.append((track_id, file_id, quality, kwargs))

        async def preview(bot, chat_id, value):
            calls.preview.append(value.id)
            return preview_result

        async def cached(bot, chat_id, value):
            calls.cached.append(value.id)
            return cached_result

        import bot.runtime as runtime

        monkeypatch.setattr(runtime, "send_audio_file", send_audio_file)
        monkeypatch.setattr(runtime, "save_file_id_retry", save_file_id)
        monkeypatch.setattr(runtime, "send_preview_file", preview)
        monkeypatch.setattr(runtime, "send_track_cached", cached)
        return calls

    async def test_file_is_sent_and_file_id_cached(self, monkeypatch, tmp_path):
        path = tmp_path / "audio.mp3"
        path.write_bytes(b"x" * 128)
        track = _fake_track()
        calls = self._install(monkeypatch, track)
        job_id = _job()

        import services.ingest as ingest

        async def resolve(session, meta, quality, **kwargs):
            return SimpleNamespace(status="downloaded", file_path=path,
                                   track=track, quality=quality, error="")

        monkeypatch.setattr(ingest, "resolve", resolve)

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "done"
        assert status["progress"] == 100.0
        assert status["telegram"] is True
        assert len(calls.send) == 1
        assert calls.save[0][:3] == (5, "FID", "aac320")
        assert calls.save[0][3]["size"] == path.stat().st_size

    async def test_failed_send_does_not_report_done(self, monkeypatch, tmp_path):
        path = tmp_path / "audio.mp3"
        path.write_bytes(b"x" * 128)
        track = _fake_track()
        calls = self._install(monkeypatch, track, send_result=None,
                              preview_result=False)
        job_id = _job()

        import services.ingest as ingest

        async def resolve(session, meta, quality, **kwargs):
            return SimpleNamespace(status="downloaded", file_path=path,
                                   track=track, quality=quality, error="")

        monkeypatch.setattr(ingest, "resolve", resolve)

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "error"
        assert "не принял" in status["error"]
        assert calls.save == []
        assert calls.preview == [5]

    async def test_failed_send_falls_back_to_preview(self, monkeypatch, tmp_path):
        path = tmp_path / "audio.mp3"
        path.write_bytes(b"x" * 128)
        track = _fake_track()
        calls = self._install(monkeypatch, track, send_result=None,
                              preview_result=True)
        job_id = _job()

        import services.ingest as ingest

        async def resolve(session, meta, quality, **kwargs):
            return SimpleNamespace(status="downloaded", file_path=path,
                                   track=track, quality=quality, error="")

        monkeypatch.setattr(ingest, "resolve", resolve)

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "done"
        assert status["preview"] is True
        assert calls.preview == [5]

    async def test_cached_file_id_path(self, monkeypatch):
        track = _fake_track(file_id="OLD-FID")
        calls = self._install(monkeypatch, track, cached_result=True)
        job_id = _job()

        import services.ingest as ingest

        async def resolve(session, meta, quality, **kwargs):
            return SimpleNamespace(status="cached", file_path=None,
                                   track=track, quality=None, error="")

        monkeypatch.setattr(ingest, "resolve", resolve)

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "done"
        assert status["telegram"] is True
        assert calls.cached == [5]

    async def test_stale_cached_id_reuploads_local_full_file(
            self, monkeypatch, tmp_path):
        """Старый file_id не должен превращать трек в 30-сек фрагмент."""
        path = tmp_path / "audio.mp3"
        path.write_bytes(b"x" * 64)
        track = _fake_track(file_id="OLD-FID", local_path=str(path))
        calls = self._install(monkeypatch, track, cached_result=False)
        job_id = _job()

        import services.ingest as ingest

        async def resolve(session, meta, quality, **kwargs):
            return SimpleNamespace(status="cached", file_path=None,
                                   track=track, quality=None, error="")

        monkeypatch.setattr(ingest, "resolve", resolve)

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "done"
        assert status["telegram"] is True
        assert calls.cached == [5]
        assert calls.send == [(5, path)]
        assert calls.save[-1][1] == "FID"

    async def test_stale_cached_id_redownloads_full_audio(
            self, monkeypatch, tmp_path):
        """Нет локальной копии — перекачиваем полный трек, а не preview."""
        path = tmp_path / "audio.mp3"
        path.write_bytes(b"x" * 64)
        track = _fake_track(file_id="OLD-FID")
        calls = self._install(monkeypatch, track, cached_result=False)
        job_id = _job()
        seen = []

        import services.ingest as ingest

        async def resolve(session, meta, quality, **kwargs):
            allow = kwargs.get("allow_cached", True)
            seen.append(allow)
            if allow:
                return SimpleNamespace(status="cached", file_path=None,
                                       track=track, quality=None, error="")
            return SimpleNamespace(status="downloaded", file_path=path,
                                   track=track, quality=quality, error="")

        monkeypatch.setattr(ingest, "resolve", resolve)

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "done"
        assert seen == [True, False]
        assert calls.cached == [5]
        assert calls.send == [(5, path)]
        assert calls.save[0][:2] == (5, "")
        assert calls.save[-1][1] == "FID"

    async def test_missing_cached_file_and_no_preview_is_error(
            self, monkeypatch):
        track = _fake_track(file_id="OLD-FID")
        self._install(monkeypatch, track, cached_result=False,
                      preview_result=False)
        job_id = _job()

        import services.ingest as ingest

        async def resolve(session, meta, quality, **kwargs):
            return SimpleNamespace(status="cached", file_path=None,
                                   track=track, quality=None, error="")

        monkeypatch.setattr(ingest, "resolve", resolve)

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "error"
        assert status["error"] == "файл не доступен"

    async def test_bot_creation_error_is_terminal_error(self, monkeypatch):
        track = _fake_track()

        async def boom():
            raise RuntimeError("no token")

        monkeypatch.setattr(tracks, "_bot", boom)
        job_id = _job()

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "error"
        assert "no token" in status["error"]
        assert (1, 5, "aac320") not in tracks._INFLIGHT

    async def test_missing_track_is_error(self, monkeypatch):
        self._install(monkeypatch, None)
        job_id = _job()
        await tracks._deliver_job(job_id, 1, 5, "aac320")
        assert tracks.JOB_STATUS[job_id]["status"] == "error"
        assert tracks.JOB_STATUS[job_id]["error"] == "нет такого трека"

    async def test_exception_in_resolve_sets_error(self, monkeypatch):
        track = _fake_track()
        self._install(monkeypatch, track)
        job_id = _job()

        import services.ingest as ingest

        async def resolve(*args, **kwargs):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(ingest, "resolve", resolve)

        await tracks._deliver_job(job_id, 1, 5, "aac320")

        status = tracks.JOB_STATUS[job_id]
        assert status["status"] == "error"
        assert "provider exploded" in status["error"]


# ------------------- Релиз 2.5: пачка («Скачать всё») -------------------

class TestBulkDownload:
    @pytest.fixture
    async def bulk_db(self, db_engine, monkeypatch):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from core.models import Base as CoreBase

        async with db_engine.begin() as conn:
            await conn.run_sync(CoreBase.metadata.create_all)
        maker = async_sessionmaker(db_engine, expire_on_commit=False,
                                   class_=AsyncSession)
        monkeypatch.setattr(tracks.runtime, "get_session", maker)
        return maker

    async def _seed_tracks(self, maker):
        from core.models import Track

        async with maker() as s:
            t1 = Track(source="yandex", external_id="y:1", title="A",
                       artist="B")
            t2 = Track(source="zaycev", external_id="z:1", title="C",
                       artist="D")
            s.add_all([t1, t2])
            await s.commit()
            return [t1.id, t2.id]

    async def test_enqueues_all_dedups_and_reports_status(self, client,
                                                          bulk_db):
        ids = await self._seed_tracks(bulk_db)
        resp = await client.post("/api/download-bulk",
                                 json={"track_ids": ids})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        jobs = {j["track_id"]: j["job"] for j in data["jobs"]}
        assert set(jobs) == set(ids)

        # повторное «скачать всё» → те же джобы (дедуп активных)
        resp2 = await client.post("/api/download-bulk",
                                  json={"track_ids": ids})
        jobs2 = {j["track_id"]: j["job"] for j in resp2.json()["jobs"]}
        assert jobs2 == jobs

        # статусы одним запросом, только свои джобы
        resp3 = await client.get("/api/jobs-bulk?ids=" + ",".join(jobs.values()))
        assert resp3.status_code == 200
        assert all(v == "new" for v in resp3.json()["jobs"].values())

    async def test_other_user_sees_nothing(self, client, app, bulk_db):
        ids = await self._seed_tracks(bulk_db)
        resp = await client.post("/api/download-bulk",
                                 json={"track_ids": ids})
        job_ids = [j["job"] for j in resp.json()["jobs"]]
        app.dependency_overrides[tg_user] = lambda: CurrentUser(id=99)
        foreign = await client.get("/api/jobs-bulk?ids=" + ",".join(job_ids))
        assert foreign.json()["jobs"] == {}

    async def test_guest_is_rejected(self):
        app = FastAPI()
        app.include_router(tracks.router)
        app.dependency_overrides[tg_user] = lambda: CurrentUser(
            id=1_000_000_000_123)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as http:
            response = await http.post("/api/download-bulk",
                                       json={"track_ids": [5]})
        assert response.status_code == 409
        assert response.json()["detail"] == "telegram_required"

    async def test_unknown_tracks_are_skipped(self, client, bulk_db):
        resp = await client.post("/api/download-bulk",
                                 json={"track_ids": [987654]})
        assert resp.status_code == 200
        assert resp.json() == {"jobs": [], "total": 0}


# ------------------- Плейлист целиком → «❤ Любимое» -------------------

class TestLikesBulk:
    @pytest.fixture
    async def likes_env(self, db_engine, monkeypatch):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from core.models import Base as CoreBase
        from server.routes import playlists

        async with db_engine.begin() as conn:
            await conn.run_sync(CoreBase.metadata.create_all)
        maker = async_sessionmaker(db_engine, expire_on_commit=False,
                                   class_=AsyncSession)
        monkeypatch.setattr(playlists.runtime, "get_session", maker)
        app = FastAPI()
        app.include_router(playlists.router)
        app.include_router(playlists.likes_router)
        app.dependency_overrides[tg_user] = lambda: CurrentUser(id=7)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as http:
            yield http, maker

    async def _seed_tracks(self, maker, n=3):
        from core.models import Track

        async with maker() as s:
            ts = [Track(source="yandex", external_id=f"y:{i}",
                        title=f"T{i}", artist="A") for i in range(n)]
            s.add_all(ts)
            await s.commit()
            return [t.id for t in ts]

    async def test_adds_all_dedups_and_reports(self, likes_env):
        http, maker = likes_env
        ids = await self._seed_tracks(maker)

        r1 = await http.post("/api/likes/bulk", json={"track_ids": ids})
        assert r1.status_code == 200
        assert r1.json()["added"] == 3

        # повторное «всё в любимое» — ничего не добавилось, всё existing
        r2 = await http.post("/api/likes/bulk", json={"track_ids": ids})
        assert r2.json()["added"] == 0
        assert r2.json()["existing"] == ids

        # GET /api/likes видит все треки
        r3 = await http.get("/api/likes")
        assert set(r3.json()["ids"]) == set(ids)

    async def test_unknown_tracks_are_skipped(self, likes_env):
        http, _maker = likes_env
        r = await http.post("/api/likes/bulk", json={"track_ids": [424242]})
        assert r.status_code == 200
        assert r.json() == {"added": 0, "ids": [], "existing": []}

    async def test_bulk_route_does_not_swallow_toggle(self, likes_env):
        # POST /api/likes/{id} не сломан новым роутом /bulk
        http, maker = likes_env
        ids = await self._seed_tracks(maker, n=1)
        r = await http.post(f"/api/likes/{ids[0]}")
        assert r.status_code == 200
        assert r.json() == {"liked": True}
