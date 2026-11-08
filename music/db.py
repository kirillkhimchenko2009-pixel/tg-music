"""Инициализация таблиц `music_*` на общем движке проекта.

`core.db.init_db()` создаёт только legacy-таблицы, поэтому бот/сервер должны
вызвать `init_music_db()`. Повторный вызов безопасен (create_all + upsert).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import AsyncSessionLocal, engine

from .models import MusicBase, MusicSource, utcnow

log = logging.getLogger("music.db")


async def init_music_db(seed_sources: bool = True) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(MusicBase.metadata.create_all)

    if seed_sources:
        try:
            from .providers import provider_specs
            async with AsyncSessionLocal() as session:
                await _seed_sources(session, provider_specs())
                await session.commit()
        except Exception as exc:  # источник не должен валить старт
            log.warning("music: не удалось заполнить music_sources: %s", exc)

    # мини-миграции для баз, созданных до появления колонок
    statements = (
        "ALTER TABLE music_download_history ADD COLUMN cache_key VARCHAR(128)",
        "ALTER TABLE music_download_history ADD COLUMN media_kind VARCHAR(16) DEFAULT 'audio'",
        "ALTER TABLE music_tracks ADD COLUMN identity_key VARCHAR(512) DEFAULT ''",
    )
    for stmt in statements:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            pass


async def _seed_sources(session: AsyncSession, specs: list[dict]) -> None:
    for spec in specs:
        provider = spec["provider"]
        row = (await session.execute(
            select(MusicSource).where(MusicSource.provider == provider)
        )).scalar_one_or_none()
        if row is None:
            session.add(MusicSource(
                provider=provider,
                display_name=spec.get("display_name", provider),
                enabled=bool(spec.get("enabled", True)),
                capabilities=spec.get("capabilities", {}),
                health=spec.get("health", "unknown"),
                last_check_at=None,
            ))
        else:
            row.display_name = spec.get("display_name", row.display_name)
            row.capabilities = spec.get("capabilities", row.capabilities)
            row.enabled = bool(spec.get("enabled", row.enabled))


@asynccontextmanager
async def music_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_music_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with AsyncSessionLocal() as session:
        yield session


async def db_healthy() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("music: БД недоступна: %s", exc)
        return False


__all__ = ["init_music_db", "music_session", "get_music_session", "db_healthy", "utcnow"]
