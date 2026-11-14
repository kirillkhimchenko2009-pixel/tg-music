"""Распределённая очередь скачиваний (mediajobs): claim, дедуп, lease,
caps-фильтр, локальный резерв и исполнитель execute()."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from music import mediajobs
from music.config import music_settings
from music.models import MediaDownloadJob, utcnow


@pytest.fixture
async def jobs_db(db_engine, monkeypatch):
    """In-memory SQLite с таблицами music_* и core_* (tracks нужен
    исполнителю); mediajobs и bot.runtime направлены на эту БД."""
    from core.models import Base as CoreBase

    async with db_engine.begin() as conn:
        await conn.run_sync(CoreBase.metadata.create_all)
    maker = async_sessionmaker(db_engine, expire_on_commit=False,
                               class_=AsyncSession)
    monkeypatch.setattr(mediajobs, "AsyncSessionLocal", maker)
    monkeypatch.setattr("bot.runtime.get_session", maker)
    return maker


def _mk_job_kwargs(track_id: int = 1, uid: int = 10, quality: str = "aac320",
                   source: str = "yandex") -> dict:
    return dict(track_id=track_id, uid=uid, quality=quality, source=source)


async def _seed_track(s, source: str = "yandex"):
    from core.models import Track
    t = Track(source=source, external_id=f"{source}:1", title="Song",
              artist="Artist", duration=180)
    s.add(t)
    await s.flush()
    return t


async def _insert_job(maker, **kwargs) -> MediaDownloadJob:
    kwargs.setdefault("attempts", 0)
    async with maker() as s:
        job = MediaDownloadJob(id=mediajobs.new_job_id(), status="new",
                               created_at=utcnow(),
                               updated_at=utcnow(), **kwargs)
        s.add(job)
        await s.commit()
        return job


# ------------------------------ claim ------------------------------

class TestClaim:
    async def test_takes_oldest_and_marks_lease(self, jobs_db):
        async with jobs_db() as s:
            j1 = await mediajobs.create_job(s, **_mk_job_kwargs(track_id=1))
            j1.created_at = utcnow() - timedelta(seconds=120)
            j2 = await mediajobs.create_job(s, **_mk_job_kwargs(track_id=2))
            j2.created_at = utcnow() - timedelta(seconds=60)
            await s.commit()

            got = await mediajobs.claim(s, "botkeep")
            assert got is not None and got.id == j1.id
            assert got.status == "leased"
            assert got.lease_host == "botkeep"
            assert got.attempts == 1
            assert got.lease_ts is not None

            got2 = await mediajobs.claim(s, "botkeep")
            assert got2 is not None and got2.id == j2.id
            # двух джоб нет — третий claim пуст
            assert await mediajobs.claim(s, "botkeep") is None

    async def test_caps_ffmpeg_filter(self, jobs_db):
        async with jobs_db() as s:
            jff = await mediajobs.create_job(
                s, **_mk_job_kwargs(track_id=1, source="ytmusic"))
            assert jff.needs_ffmpeg is True
            jok = await mediajobs.create_job(
                s, **_mk_job_kwargs(track_id=2, source="jamendo"))
            jok.created_at = utcnow() - timedelta(seconds=30)  # старее jff
            await s.commit()

            # воркер без ffmpeg: ffmpeg-джоба невидима, берётся прочая
            got = await mediajobs.claim(s, "weak", caps_ffmpeg=False)
            assert got.id == jok.id
            assert await mediajobs.claim(s, "weak", caps_ffmpeg=False) is None
            # воркер с ffmpeg добирает оставшуюся
            got2 = await mediajobs.claim(s, "strong", caps_ffmpeg=True)
            assert got2.id == jff.id

    async def test_min_age_reserve_does_not_steal_fresh_jobs(self, jobs_db):
        async with jobs_db() as s:
            fresh = await mediajobs.create_job(
                s, **_mk_job_kwargs(track_id=1))
            await s.commit()
            # локальный резерв Render: свежую джобу не трогает — её
            # заберёт основной воркер, который поллит чаще
            assert await mediajobs.claim(s, "render-reserve",
                                         min_age_sec=90) is None
            got = await mediajobs.claim(s, "botkeep")
            assert got.id == fresh.id
            # вернём в очередь и состарим — резерв берёт
            got.status = "new"
            got.lease_ts = None
            got.created_at = utcnow() - timedelta(seconds=120)
            await s.commit()
            old = await mediajobs.claim(s, "render-reserve", min_age_sec=90)
            assert old.id == fresh.id


# --------------------------- дедуп и лимит ---------------------------

class TestDedup:
    async def test_find_active_and_count(self, jobs_db):
        async with jobs_db() as s:
            j = await mediajobs.create_job(s, **_mk_job_kwargs(track_id=7))
            await s.commit()
            again = await mediajobs.find_active(s, 7, 10)
            assert again is not None and again.id == j.id
            assert await mediajobs.find_active(s, 7, 11) is None  # чужой юзер
            assert await mediajobs.count_active(s, 10) == 1
        await mediajobs.mark_done(j.id, file_id="fid")
        async with jobs_db() as s2:
            assert await mediajobs.find_active(s2, 7, 10) is None
            assert await mediajobs.count_active(s2, 10) == 0


# ------------------------------ lease TTL ------------------------------

class TestStaleLease:
    async def test_expired_lease_returns_to_queue(self, jobs_db):
        async with jobs_db() as s:
            j = await mediajobs.create_job(s, **_mk_job_kwargs(track_id=5))
            await s.commit()
            got = await mediajobs.claim(s, "botkeep")
            # воркер умер: lease старше TTL
            got.lease_ts = utcnow() - timedelta(
                seconds=music_settings.lease_ttl_sec + 5)
            await s.commit()
            touched = await mediajobs.release_stale(s)
            assert touched == 1
            again = await mediajobs.claim(s, "serv00")
            assert again.id == j.id
            assert again.attempts == 2
            assert again.lease_host == "serv00"

    async def test_max_attempts_turns_error(self, jobs_db):
        async with jobs_db() as s:
            j = await mediajobs.create_job(s, **_mk_job_kwargs(track_id=5))
            await s.commit()
            got = await mediajobs.claim(s, "botkeep")
            got.attempts = music_settings.media_max_attempts
            got.lease_ts = utcnow() - timedelta(
                seconds=music_settings.lease_ttl_sec + 5)
            await s.commit()
            await mediajobs.release_stale(s)
        async with jobs_db() as s2:
            row = await s2.get(MediaDownloadJob, j.id)
            assert row.status == "error"
            assert "lease" in (row.error or "")


# ------------------------------ view ------------------------------

def test_public_view_contract():
    job = MediaDownloadJob(id="j1", status="leased", progress=42.5,
                           telegram=True)
    assert mediajobs.public_view(job) == {
        "status": "downloading", "progress": 42.5,
        "telegram": True, "preview": False, "error": "",
    }
    queued = MediaDownloadJob(id="j2", status="new", error=None)
    view = mediajobs.public_view(queued)
    assert view["status"] == "queued" and view["progress"] == 0.0


def test_needs_ffmpeg_sources():
    assert mediajobs.needs_ffmpeg_for("ytmusic")
    assert mediajobs.needs_ffmpeg_for("soundcloud")
    assert not mediajobs.needs_ffmpeg_for("yandex")
    assert not mediajobs.needs_ffmpeg_for("zvuk")
    assert not mediajobs.needs_ffmpeg_for("")


# ---------------------------- исполнитель ----------------------------

@pytest.fixture
def bot_io(monkeypatch):
    """Telegram-отправка и кеш file_id — фейковые: execute() без сети."""
    calls: dict = {"audio": [], "save": [], "preview": 0}

    async def fake_bot():
        return object()

    async def fake_send_audio_file(bot, chat_id, track, path):
        calls["audio"].append((chat_id, track.id, str(path)))
        return "tg-fid-1"

    async def fake_send_track_cached(bot, chat_id, track):
        return False

    async def fake_send_preview_file(bot, chat_id, track):
        calls["preview"] += 1
        return False

    async def fake_save_file_id_retry(track_id, file_id, quality=None, **kw):
        calls["save"].append((track_id, file_id, quality))
        return True

    monkeypatch.setattr(mediajobs, "_bot", fake_bot)
    monkeypatch.setattr("bot.runtime.send_audio_file", fake_send_audio_file)
    monkeypatch.setattr("bot.runtime.send_track_cached", fake_send_track_cached)
    monkeypatch.setattr("bot.runtime.send_preview_file", fake_send_preview_file)
    monkeypatch.setattr("bot.runtime.save_file_id_retry", fake_save_file_id_retry)
    return calls


def _patch_resolve(monkeypatch, res):
    async def fake_resolve(session, meta, quality, user_id=None,
                           on_progress=None, allow_cached=True):
        return res
    monkeypatch.setattr("services.ingest.resolve", fake_resolve)


class TestExecute:
    async def test_downloads_sends_caches_and_cleans(self, jobs_db, bot_io,
                                                     monkeypatch, tmp_path):
        f = tmp_path / "song.mp3"
        f.write_bytes(b"ID3" + b"x" * 64)
        async with jobs_db() as s:
            t = await _seed_track(s, source="yandex")
            await s.commit()
        job = await _insert_job(jobs_db, track_id=t.id, uid=555,
                                quality="aac320", needs_ffmpeg=False,
                                attempts=1)
        _patch_resolve(monkeypatch, SimpleNamespace(
            track=t, file_path=f, quality="aac320",
            status="downloaded", error=None))

        outcome = await mediajobs.execute(job)
        assert outcome == "done"
        async with jobs_db() as s2:
            row = await s2.get(MediaDownloadJob, job.id)
            assert row.status == "done"
            assert row.file_id == "tg-fid-1"
            assert row.telegram is True and row.preview is False
            assert row.progress == 100.0
        # файл ушёл в чат юзера (uid == chat_id), file_id закеширован,
        # путь воркера в Neon НЕ сохраняется (файл удалён)
        assert bot_io["audio"] == [(555, t.id, str(f))]
        assert bot_io["save"] == [(t.id, "tg-fid-1", "aac320")]
        assert not f.exists()

    async def test_not_found_marks_error_without_retry(self, jobs_db, bot_io,
                                                       monkeypatch):
        async with jobs_db() as s:
            t = await _seed_track(s)
            await s.commit()
        job = await _insert_job(jobs_db, track_id=t.id, uid=555,
                                quality="aac320", attempts=1)
        _patch_resolve(monkeypatch, SimpleNamespace(
            track=t, file_path=None, quality=None,
            status="not_found", error="нет совпадений"))

        outcome = await mediajobs.execute(job)
        assert outcome == "done"
        async with jobs_db() as s2:
            row = await s2.get(MediaDownloadJob, job.id)
            assert row.status == "error"
            assert row.error == "нет совпадений"
            assert row.telegram is False
        assert bot_io["audio"] == []
        assert bot_io["preview"] == 1  # фрагмент тоже не удался — но попытка была

    async def test_crash_returns_job_to_queue_while_attempts_left(self, jobs_db,
                                                                  bot_io,
                                                                  monkeypatch):
        async with jobs_db() as s:
            t = await _seed_track(s)
            await s.commit()
        job = await _insert_job(jobs_db, track_id=t.id, uid=555,
                                quality="aac320", attempts=1)

        async def boom(*a, **k):
            raise RuntimeError("сеть отвалилась")
        monkeypatch.setattr("services.ingest.resolve", boom)

        outcome = await mediajobs.execute(job)
        assert outcome == "retry"
        async with jobs_db() as s2:
            row = await s2.get(MediaDownloadJob, job.id)
            assert row.status == "new"          # снова в очереди
            assert row.error is None            # юзеру пока не сообщаем
            assert row.lease_host is None

    async def test_crash_after_max_attempts_is_terminal(self, jobs_db, bot_io,
                                                        monkeypatch):
        async with jobs_db() as s:
            t = await _seed_track(s)
            await s.commit()
        job = await _insert_job(
            jobs_db, track_id=t.id, uid=555, quality="aac320",
            attempts=music_settings.media_max_attempts)

        async def boom(*a, **k):
            raise RuntimeError("снова упало")
        monkeypatch.setattr("services.ingest.resolve", boom)

        outcome = await mediajobs.execute(job)
        assert outcome == "done"
        async with jobs_db() as s2:
            row = await s2.get(MediaDownloadJob, job.id)
            assert row.status == "error"
            assert "снова упало" in (row.error or "")


# ----------------------------- poller -----------------------------

class TestPoller:
    async def test_tick_empty_and_job(self, jobs_db, monkeypatch):
        poller = mediajobs.MediaJobPoller(name="t", poll_sec=0.01)
        monkeypatch.setattr(mediajobs, "AsyncSessionLocal", jobs_db)

        async def fake_execute(job):
            return "done"
        monkeypatch.setattr(mediajobs, "execute", fake_execute)

        assert await poller.tick() == "empty"          # очередь пуста
        async with jobs_db() as s:
            j = await mediajobs.create_job(s, **_mk_job_kwargs(track_id=3))
            j.created_at = utcnow() - timedelta(seconds=5)
            await s.commit()
        assert await poller.tick() == "done"           # джоба забрана
        async with jobs_db() as s2:
            row = await s2.get(MediaDownloadJob, j.id)
            assert row.status == "leased"
            assert row.lease_host == "t"
