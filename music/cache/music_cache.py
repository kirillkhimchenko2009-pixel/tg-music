"""Файловый кэш и Telegram file_id: не скачивать и не загружать дважды."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from ..config import music_settings
from ..models import MusicCachedFile, utcnow
from ..storage import sha256_file

log = logging.getLogger("music.cache")


def cache_path(sha256: str, ext: str = ".bin") -> Path:
    sha = (sha256 or "0" * 64).lower()
    if not ext.startswith("."):
        ext = "." + ext
    folder = music_settings.cache_dir / sha[:2]
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{sha}{ext}"


async def lookup_cached(session, sha256: str | None = None, *,
                        cache_key: str | None = None,
                        identity_key: str | None = None,
                        allow_expired: bool = False) -> MusicCachedFile | None:
    q = select(MusicCachedFile)
    if cache_key:
        q = q.where(MusicCachedFile.cache_key == cache_key)
    elif sha256:
        q = q.where(MusicCachedFile.sha256 == sha256)
    elif identity_key:
        q = q.where(MusicCachedFile.identity_key == identity_key)
    else:
        return None
    row = (await session.execute(q.order_by(MusicCachedFile.last_used_at.desc()))).scalars().first()
    if not row:
        return None
    path = Path(row.path)
    if not path.exists() or path.stat().st_size != row.size:
        await session.delete(row)
        await session.commit()
        return None
    if not allow_expired and row.expires_at and row.expires_at < utcnow():
        return None
    row.last_used_at = utcnow()
    return row


async def store_cached(session, source_path: Path, *, sha256: str, file_format: str,
                       duration: float = 0.0, media_kind: str = "audio",
                       identity_key: str = "", cache_key: str | None = None,
                       quality_category: str = "UNKNOWN",
                       bitrate_kbps: int | None = None,
                       sample_rate: int | None = None,
                       title: str = "", artist: str = "",
                       move: bool = True) -> MusicCachedFile:
    ext = "." + file_format.lstrip(".")
    dest = cache_path(sha256, ext)
    if Path(source_path).resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(source_path), dest)
        else:
            shutil.copy2(source_path, dest)
    key = cache_key or sha256[:32]
    row = (await session.execute(
        select(MusicCachedFile).where(MusicCachedFile.cache_key == key)
    )).scalar_one_or_none()
    expires = utcnow() + timedelta(days=music_settings.cache_ttl_days)
    if row is None:
        row = MusicCachedFile(
            cache_key=key, identity_key=identity_key, path=str(dest),
            size=dest.stat().st_size, sha256=sha256, format=file_format,
            duration=duration, media_kind=media_kind, expires_at=expires,
            quality_category=quality_category, bitrate_kbps=bitrate_kbps,
            sample_rate=sample_rate, title=title, artist=artist)
        session.add(row)
    else:
        row.path = str(dest)
        row.size = dest.stat().st_size
        row.sha256 = sha256
        row.format = file_format
        row.duration = duration
        row.media_kind = media_kind
        row.quality_category = quality_category
        row.bitrate_kbps = bitrate_kbps
        row.sample_rate = sample_rate
        row.title = title or row.title
        row.artist = artist or row.artist
        row.expires_at = expires
        row.last_used_at = utcnow()
    await session.commit()
    return row


async def set_telegram_file(session, cache_key: str, file_id: str,
                            file_unique_id: str = "") -> None:
    row = (await session.execute(
        select(MusicCachedFile).where(MusicCachedFile.cache_key == cache_key)
    )).scalar_one_or_none()
    if row:
        row.telegram_file_id = file_id
        row.telegram_file_unique_id = file_unique_id
        row.last_used_at = utcnow()
        await session.commit()


async def cleanup_cache(session, max_mb: int | None = None,
                        ttl_days: int | None = None) -> dict[str, int]:
    """Удаляет просроченные и самые старые файлы сверх лимита."""
    max_bytes = (max_mb if max_mb is not None else music_settings.cache_max_mb) * 1024 * 1024
    ttl = (ttl_days if ttl_days is not None else music_settings.cache_ttl_days)
    cutoff = utcnow() - timedelta(days=ttl)
    removed_files = removed_rows = freed = 0

    rows = (await session.execute(
        select(MusicCachedFile).order_by(MusicCachedFile.last_used_at.asc())
    )).scalars().all()
    total = sum(r.size for r in rows)
    for row in rows:
        expired = bool(row.expires_at and row.expires_at < utcnow())
        too_big = total > max_bytes
        if not expired and not too_big:
            continue
        try:
            path = Path(row.path)
            if path.exists():
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                removed_files += 1
                freed += size
        except OSError as exc:
            log.debug("cache cleanup %s: %s", row.path, exc)
        total -= row.size
        await session.delete(row)
        removed_rows += 1
    await session.commit()
    return {"files": removed_files, "rows": removed_rows, "freed_bytes": freed}


async def drop_cache_row(session, cache_key: str) -> None:
    await session.execute(delete(MusicCachedFile).where(MusicCachedFile.cache_key == cache_key))
    await session.commit()


__all__ = ["cache_path", "lookup_cached", "store_cached", "set_telegram_file",
           "cleanup_cache", "drop_cache_row", "sha256_file", "utcnow", "datetime"]
