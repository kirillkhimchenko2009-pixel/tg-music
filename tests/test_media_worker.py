"""Распределённая очередь скачиваний (mediajobs): claim, дедуп, lease,
caps-фильтр, локальный резерв и исполнитель execute()."""
from __future__ import annotations

import asyncio
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
    status = kwargs.pop("status", "new")
    async with maker() as s:
        job = MediaDownloadJob(id=mediajobs.new_job_id(), status=status,
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


# --------------------- Релиз 2.5: батч-хелперы и таймаут джобы ---------------------

class TestCancelAndStatuses:
    async def test_cancel_jobs_touches_only_new(self, jobs_db):
        j_new = await _insert_job(jobs_db, track_id=1, uid=10, attempts=1)
        j_leased = await _insert_job(jobs_db, track_id=2, uid=10, attempts=1,
                                     status="leased")
        j_done = await _insert_job(jobs_db, track_id=3, uid=10, attempts=1,
                                   status="done")
        async with jobs_db() as s:
            n = await mediajobs.cancel_jobs(s, [j_new.id, j_leased.id,
                                                j_done.id])
            assert n == 1                       # только 'new'
            st = await mediajobs.statuses_of(
                s, [j_new.id, j_leased.id, j_done.id])
        assert st[j_new.id] == "canceled"
        assert st[j_leased.id] == "leased"      # доделывается воркером
        assert st[j_done.id] == "done"

    async def test_statuses_of_empty_and_unknown(self, jobs_db):
        async with jobs_db() as s:
            assert await mediajobs.statuses_of(s, []) == {}
            assert await mediajobs.statuses_of(s, ["нет-такого"]) == {}


class TestJobTimeout:
    async def test_hanging_job_cancelled_and_sent_back(self, jobs_db,
                                                       monkeypatch):
        poller = mediajobs.MediaJobPoller(name="t", poll_sec=0.01)
        monkeypatch.setattr(mediajobs, "AsyncSessionLocal", jobs_db)
        old = music_settings.job_timeout_sec
        music_settings.job_timeout_sec = 1      # таймаут на 1 с

        async def hanging(job):                 # вечное «висение» (upload)
            await asyncio.sleep(60)
        monkeypatch.setattr(mediajobs, "execute", hanging)
        try:
            async with jobs_db() as s:
                j = await mediajobs.create_job(s, **_mk_job_kwargs(track_id=7))
                j.created_at = utcnow() - timedelta(seconds=5)
                await s.commit()
                jid = j.id
            outcome = await asyncio.wait_for(poller.tick(), timeout=10)
            assert outcome == "retry"           # попытки ещё есть → в очередь
            async with jobs_db() as s2:
                row = await s2.get(MediaDownloadJob, jid)
                assert row.status == "new"
                assert row.lease_host is None
        finally:
            music_settings.job_timeout_sec = old

    async def test_hanging_job_after_max_attempts_is_error(self, jobs_db,
                                                           monkeypatch):
        poller = mediajobs.MediaJobPoller(name="t", poll_sec=0.01)
        monkeypatch.setattr(mediajobs, "AsyncSessionLocal", jobs_db)
        old = music_settings.job_timeout_sec
        music_settings.job_timeout_sec = 1

        async def hanging(job):
            await asyncio.sleep(60)
        monkeypatch.setattr(mediajobs, "execute", hanging)
        try:
            async with jobs_db() as s:
                j = await _insert_job(
                    jobs_db, track_id=8, uid=10, quality="aac320",
                    attempts=music_settings.media_max_attempts - 1)
                jid = j.id
            # claim поднимет attempts до max → таймаут = терминальная ошибка
            outcome = await asyncio.wait_for(poller.tick(), timeout=10)
            assert outcome == "done"
            async with jobs_db() as s2:
                row = await s2.get(MediaDownloadJob, jid)
                assert row.status == "error"
        finally:
            music_settings.job_timeout_sec = old


# --------------------- Релиз 1.5: хаб бота через очередь ---------------------

def test_candidate_meta_mapping():
    from music.domain import TrackCandidate
    cand = TrackCandidate(provider="jamendo", external_id="42", title="Song",
                          artist="Artist", album="Alb", duration=200,
                          year=2020, isrc="ISRC1", explicit=True,
                          source_url="https://example.com/t",
                          license_name="CC-BY 4.0")
    meta = mediajobs.candidate_meta(cand)
    assert (meta.source, meta.external_id) == ("jamendo", "42")
    assert meta.title == "Song" and meta.artist == "Artist"
    assert meta.duration == 200 and meta.year == 2020
    assert meta.isrc == "ISRC1" and meta.explicit is True
    assert meta.page_url == "https://example.com/t"
    assert meta.license == "CC-BY 4.0"


class _FakeMsg:
    def __init__(self):
        self.edits: list[tuple[str, dict]] = []

    async def edit_text(self, text, **kw):
        self.edits.append((text, kw))


class TestRemoteWatcher:
    """Вотчер статуса джобы: прогресс из Neon → сообщение бота.

    Состояние джобы меняем ДО старта вотчера: тесты используют общую
    in-memory SQLite (StaticPool), одновременные сессии на одном
    соединении ненадёжны; в проде это отдельные соединения к Neon."""

    async def _run(self, monkeypatch, jobs_db, job_id, *, delay=0.01,
                   max_sec=5.0):
        import music.telegram.handlers as hmod
        from music.telegram.progress import ProgressView
        monkeypatch.setattr(hmod, "AsyncSessionLocal", jobs_db)
        msg = _FakeMsg()
        view = ProgressView(msg, title="T", min_interval=0.0)
        task = asyncio.create_task(
            hmod._watch_remote_job(view, job_id, "https://fb",
                                   delay=delay, max_sec=max_sec))
        return task, msg

    async def test_done_finishes_with_success(self, jobs_db, monkeypatch):
        job = await _insert_job(jobs_db, track_id=1, uid=10,
                                quality="aac320", attempts=1)
        await mediajobs.mark_done(job.id, file_id="f")
        task, msg = await self._run(monkeypatch, jobs_db, job.id)
        await asyncio.wait_for(task, timeout=5)
        last = msg.edits[-1][0]
        assert "Готово" in last and "файл отправлен в чат" in last

    async def test_preview_note_shown(self, jobs_db, monkeypatch):
        job = await _insert_job(jobs_db, track_id=2, uid=10, quality="", attempts=1)
        await mediajobs.mark_done(job.id, file_id="", preview=True)
        task, msg = await self._run(monkeypatch, jobs_db, job.id)
        await asyncio.wait_for(task, timeout=5)
        assert "фрагмент" in msg.edits[-1][0]

    async def test_error_finishes_with_reason(self, jobs_db, monkeypatch):
        job = await _insert_job(jobs_db, track_id=3, uid=10,
                                quality="aac320", attempts=1)
        await mediajobs.mark_error(job.id, "источник недоступен", retry=False)
        task, msg = await self._run(monkeypatch, jobs_db, job.id)
        await asyncio.wait_for(task, timeout=5)
        last = msg.edits[-1][0]
        assert ("Не удалось получить файл" in last
                and "источник недоступен" in last)

    async def test_progress_drawn_from_db(self, jobs_db, monkeypatch):
        job = await _insert_job(jobs_db, track_id=5, uid=10,
                                quality="aac320", attempts=1)
        await mediajobs.touch_progress(job.id, 55.0)
        task, msg = await self._run(monkeypatch, jobs_db, job.id, max_sec=0.2)
        await asyncio.wait_for(task, timeout=5)
        rendered = "".join(text for text, _ in msg.edits)
        assert "55%" in rendered

    async def test_deadline_exits_without_success_edit(self, jobs_db,
                                                       monkeypatch):
        job = await _insert_job(jobs_db, track_id=4, uid=10, quality="", attempts=1)
        task, msg = await self._run(monkeypatch, jobs_db, job.id, max_sec=0.05)
        await asyncio.wait_for(task, timeout=5)
        assert all("Готово" not in text for text, _ in msg.edits)
