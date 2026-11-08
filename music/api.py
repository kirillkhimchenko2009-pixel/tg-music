"""HTTP API музыкальной подсистемы: health, источники, статистика.

Секреты и содержимое кредов наружу не отдаются. Endpoint'ы доступны
на том же FastAPI-сервере, что и legacy API (`/api/music/*`).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from .db import AsyncSessionLocal
from .health import health_dict
from .models import MusicCachedFile, MusicDownloadHistory, MusicTrack
from .providers.registry import registry

log = logging.getLogger("music.api")

router = APIRouter(prefix="/api/music", tags=["music"])


@router.get("/health")
async def music_health(deep: bool = Query(False, description="Проверить внешние API")):
    data = await health_dict(deep=deep)
    data["version"] = "1.0.0"
    return data


@router.get("/sources")
async def music_sources():
    snapshot = registry.snapshot()
    return {
        "sources": [
            {
                "provider": name,
                "display_name": info.get("display_name", name),
                "enabled": info.get("enabled", False),
                "configured": info.get("configured", False),
                "health": info.get("health", "unknown"),
                "capabilities": info.get("capabilities", {}),
            }
            for name, info in snapshot.items()
        ]
    }


@router.get("/stats")
async def music_stats():
    async with AsyncSessionLocal() as session:
        tracks = int((await session.execute(
            select(func.count()).select_from(MusicTrack))).scalar() or 0)
        downloads = int((await session.execute(
            select(func.count()).select_from(MusicDownloadHistory))).scalar() or 0)
        cached_files = int((await session.execute(
            select(func.count()).select_from(MusicCachedFile))).scalar() or 0)
        cached_bytes = int((await session.execute(
            select(func.coalesce(func.sum(MusicCachedFile.size), 0))
        )).scalar() or 0)
    return {
        "tracks": tracks,
        "downloads": downloads,
        "cached_files": cached_files,
        "cached_bytes": cached_bytes,
    }


__all__ = ["router"]
