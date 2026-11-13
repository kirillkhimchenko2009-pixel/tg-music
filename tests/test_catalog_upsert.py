"""Каталог: upsert с whitelist колонок Track + page_url/license в dirty-check
(TrackMeta шире модели — 'downloadable' не должен падать в INSERT)."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

from core.models import Base, Track
from services.catalog import upsert_track
from services.providers.base import TrackMeta


def _meta(ext: str = "t1", **kw) -> TrackMeta:
    base = dict(source="audius", external_id=ext, title="КИНО", artist="Quok",
                duration=262, page_url="https://audius.co/quok/кино",
                license=None, downloadable=False)
    base.update(kw)
    return TrackMeta(**base)


@pytest.fixture
async def core_session():
    """In-memory SQLite с таблицами core (Track) — изолированно."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s
    await engine.dispose()


class TestUpsertWhitelist:
    async def test_insert_with_new_fields(self, core_session):
        """TrackMeta содержит поля, которых нет в Track — INSERT не падает."""
        t = await upsert_track(core_session, _meta())
        await core_session.commit()
        assert t.page_url == "https://audius.co/quok/кино"
        assert t.license is None
        assert t.title == "КИНО"

    async def test_extra_field_downloadable_not_column(self, core_session):
        """downloadable — режим реестра, а не колонка: в БД не попадает."""
        t = await upsert_track(core_session, _meta(downloadable=True))
        await core_session.commit()
        cols = t.__table__.columns.keys()
        assert "downloadable" not in cols

    async def test_dirty_check_includes_page_and_license(self, core_session):
        t = await upsert_track(core_session, _meta())
        await core_session.commit()
        # обновление page_url/license попадает в dirty-check
        t2 = await upsert_track(core_session,
                                _meta(license="CC-BY 4.0",
                                      page_url="https://audius.co/quok/новая"))
        await core_session.commit()
        assert t2.license == "CC-BY 4.0"
        assert t2.page_url == "https://audius.co/quok/новая"

    async def test_no_dirty_write_when_unchanged(self, core_session):
        t = await upsert_track(core_session, _meta())
        await core_session.commit()
        before = t.title
        t2 = await upsert_track(core_session, _meta())    # то же самое
        assert t2.title == before
        assert t2 not in core_session.dirty               # не переписываем каталог

    async def test_license_long_value(self, core_session):
        """SQLite не строга к VARCHAR(64); PG-движок проверяется миграцией."""
        t = await upsert_track(core_session, _meta(license="L" * 200))
        await core_session.commit()
        assert t.license == "L" * 200
