"""run_batch (bot/batch.py): дедуп скачивания — уже скачанные (кеш file_id)
не ставятся в очередь повторно, качаются только новодобавленные."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import batch
from bot.batch import run_batch
from music import mediajobs
from services.providers.base import TrackMeta


@pytest.fixture
async def batch_db(db_engine, monkeypatch):
    """In-memory SQLite со всеми таблицами; bot.runtime.get_session и
    mediajobs направлены на неё (как в test_media_worker)."""
    from core.models import Base as CoreBase

    async with db_engine.begin() as conn:
        await conn.run_sync(CoreBase.metadata.create_all)
    maker = async_sessionmaker(db_engine, expire_on_commit=False,
                               class_=AsyncSession)
    monkeypatch.setattr("bot.runtime.get_session", maker)
    monkeypatch.setattr(mediajobs, "AsyncSessionLocal", maker)
    return maker


def _bot_stub():
    """Фейковый бот: send_message возвращает сообщение-заглушку с логом правок."""

    class _Msg:
        def __init__(self):
            self.texts: list[str] = []

        async def edit_text(self, text, **kw):
            self.texts.append(text)

    class _Bot:
        def __init__(self):
            self.msg = _Msg()

        async def send_message(self, chat_id, text, **kw):
            self.msg.texts.append(text)
            return self.msg

    return _Bot()


def _metas(n: int) -> list[TrackMeta]:
    return [TrackMeta(source="yandex", external_id=f"y:{i}", title=f"T{i}",
                      artist="A", duration=180) for i in range(n)]


async def _wait_done(monkeypatch):
    """Вотчер батча должен сразу увидеть 'done' — иначе ждал бы 45 минут."""
    async def fake_statuses(session, job_ids):
        return {jid: "done" for jid in job_ids}
    monkeypatch.setattr(mediajobs, "statuses_of", fake_statuses)


class TestRunBatchDedup:
    async def test_cached_skipped_new_queued(self, batch_db, monkeypatch):
        """1 из 3 уже скачан: 2 джобы в очередь, «из кеша: 1» в сводке."""
        from sqlalchemy import func, select

        from core.models import Track
        from music.models import MediaDownloadJob

        await _wait_done(monkeypatch)
        metas = _metas(3)
        bot = _bot_stub()
        # предзаполняем кеш: первый трек уже скачан ранее
        async with batch_db() as s:
            s.add(Track(source="yandex", external_id="y:0", title="T0",
                        artist="A", file_id="tg-cached-1"))
            await s.commit()

        sent, failed = await run_batch(bot, 100, 200, metas, "aac320")
        assert sent == 2 and failed == []
        texts = "\n".join(bot.msg.texts)
        assert "В очередь: <b>2</b>" in texts
        assert "Уже скачано: <b>1</b>" in texts
        assert "из кеша: <b>1</b>" in texts
        async with batch_db() as s:
            n_jobs = (await s.execute(
                select(func.count()).select_from(MediaDownloadJob))).scalar()
            n_tracks = (await s.execute(
                select(func.count()).select_from(Track))).scalar()
        assert n_jobs == 2          # закешированный не ставится
        assert n_tracks == 3        # все треки заведены в каталог

    async def test_all_cached_no_jobs(self, batch_db, monkeypatch):
        """Все уже скачаны: джоб нет, бот честно говорит об этом."""
        from sqlalchemy import func, select

        from core.models import Track
        from music.models import MediaDownloadJob

        await _wait_done(monkeypatch)
        metas = _metas(2)
        bot = _bot_stub()
        async with batch_db() as s:
            for i in range(2):
                s.add(Track(source="yandex", external_id=f"y:{i}",
                            title=f"T{i}", artist="A", file_id=f"f{i}"))
            await s.commit()

        sent, failed = await run_batch(bot, 100, 201, metas, "aac320")
        assert sent == 0 and failed == []
        texts = "\n".join(bot.msg.texts)
        assert "уже скачаны" in texts
        async with batch_db() as s:
            n_jobs = (await s.execute(
                select(func.count()).select_from(MediaDownloadJob))).scalar()
        assert n_jobs == 0

    async def test_cancel_clears_batch_jobs(self, batch_db, monkeypatch):
        """После батча BATCH_JOBS юзера очищается (контракт cb_extstop)."""
        await _wait_done(monkeypatch)
        bot = _bot_stub()
        await run_batch(bot, 100, 202, _metas(1), "aac320")
        assert batch.BATCH_JOBS.get(202) in (None, [])
