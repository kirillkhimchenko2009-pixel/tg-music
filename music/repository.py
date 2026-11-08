"""Репозиторий музыкальной системы: треки, история, отчёты, избранное."""
from __future__ import annotations

import base64
import hashlib
import logging
from datetime import timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import music_settings
from .domain import CoverAnalysis, PreparedTrack, TrackCandidate
from .enums import JobStatus, PermissionStatus
from .models import (MusicAlbum, MusicArtist, MusicAudioQualityReport,
                     MusicCachedFile, MusicCoverQualityReport, MusicDownloadHistory,
                     MusicDownloadJob, MusicSource, MusicTrack, MusicUser,
                     MusicUserFavorite, MusicUserSettings, utcnow)
from .search.normalizer import normalize_identity

log = logging.getLogger("music.repository")


async def ensure_user(session: AsyncSession, user_id: int, username: str | None = None,
                      first_name: str = "") -> MusicUser:
    user = await session.get(MusicUser, int(user_id))
    if user is None:
        user = MusicUser(user_id=int(user_id), username=username,
                         first_name=first_name or "", last_seen_at=utcnow())
        try:
            async with session.begin_nested():
                session.add(user)
                await session.flush()
        except IntegrityError:
            user = await session.get(MusicUser, int(user_id))
            return user  # type: ignore[return-value]
    else:
        user.username = username or user.username
        user.first_name = first_name or user.first_name
        user.last_seen_at = utcnow()
    return user


async def _artist(session: AsyncSession, name: str) -> MusicArtist | None:
    name = (name or "").strip()
    if not name:
        return None
    norm = normalize_identity(name)[:255]
    row = (await session.execute(
        select(MusicArtist).where(MusicArtist.normalized == norm)
    )).scalar_one_or_none()
    if row is None:
        row = MusicArtist(name=name[:255], normalized=norm)
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            row = (await session.execute(
                select(MusicArtist).where(MusicArtist.normalized == norm)
            )).scalar_one_or_none()
    return row


async def _album(session: AsyncSession, title: str, artist_id: int | None,
                 year: int | None, cover_url: str | None) -> MusicAlbum | None:
    title = (title or "").strip()
    if not title:
        return None
    norm = normalize_identity(title)[:255]
    q = select(MusicAlbum).where(MusicAlbum.normalized == norm)
    q = q.where(MusicAlbum.artist_id == artist_id) if artist_id else q.where(
        MusicAlbum.artist_id.is_(None))
    row = (await session.execute(q)).scalar_one_or_none()
    if row is None:
        row = MusicAlbum(title=title[:255], normalized=norm, artist_id=artist_id,
                         year=year, cover_url=cover_url)
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            row = (await session.execute(q)).scalar_one_or_none()
    elif cover_url and not row.cover_url:
        row.cover_url = cover_url
    return row


async def upsert_track(session: AsyncSession, candidate: TrackCandidate) -> MusicTrack:
    artist = await _artist(session, candidate.artist)
    album = await _album(session, candidate.album, artist.id if artist else None,
                         candidate.year, candidate.cover_url)
    row = (await session.execute(
        select(MusicTrack).where(MusicTrack.provider == candidate.provider,
                                 MusicTrack.external_id == str(candidate.external_id))
    )).scalar_one_or_none()
    fields = dict(
        title=(candidate.title or "Без названия")[:255],
        artist_id=artist.id if artist else None,
        album_id=album.id if album else None,
        artist_name=(candidate.artist or "")[:255],
        album_name=(candidate.album or "")[:255],
        duration=int(candidate.duration or 0),
        version=candidate.version.value,
        isrc=(candidate.isrc or None),
        source_url=candidate.source_url or None,
        cover_url=candidate.cover_url or None,
        license_name=(candidate.license_name or None),
        license_url=(candidate.license_url or None),
        explicit=bool(candidate.explicit),
        identity_key=candidate.identity_key[:512],
        extra=dict(candidate.extra or {}),
    )
    if row is None:
        row = MusicTrack(provider=candidate.provider,
                         external_id=str(candidate.external_id)[:255], **fields)
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            row = (await session.execute(
                select(MusicTrack).where(MusicTrack.provider == candidate.provider,
                                         MusicTrack.external_id == str(candidate.external_id))
            )).scalar_one()
    else:
        for key, value in fields.items():
            if value not in (None, ""):
                setattr(row, key, value)
        row.updated_at = utcnow()
    candidate.track_id = row.id
    return row


async def record_job(session: AsyncSession, job_id: str, user_id: int,
                     candidate: TrackCandidate | None, *,
                     status: JobStatus = JobStatus.QUEUED,
                     payload: dict | None = None) -> MusicDownloadJob:
    row = await session.get(MusicDownloadJob, job_id)
    if row is None:
        row = MusicDownloadJob(
            id=job_id, user_id=int(user_id), status=status.value,
            provider=candidate.provider if candidate else "",
            external_id=str(candidate.external_id)[:255] if candidate else "",
            payload=payload or (candidate.as_dict() if candidate else {}))
        session.add(row)
        await session.flush()
    else:
        row.status = status.value
        if candidate:
            row.provider = candidate.provider
            row.external_id = str(candidate.external_id)[:255]
    return row


async def update_job(session: AsyncSession, job_id: str, **fields) -> None:
    row = await session.get(MusicDownloadJob, job_id)
    if not row:
        return
    for key, value in fields.items():
        if key == "status" and isinstance(value, JobStatus):
            value = value.value
        if hasattr(row, key):
            setattr(row, key, value)
    await session.flush()


async def count_active_jobs(session: AsyncSession, user_id: int) -> int:
    return int((await session.execute(
        select(func.count()).select_from(MusicDownloadJob).where(
            MusicDownloadJob.user_id == int(user_id),
            MusicDownloadJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
    )).scalar() or 0)


async def count_daily_downloads(session: AsyncSession, user_id: int) -> int:
    since = utcnow() - timedelta(days=1)
    return int((await session.execute(
        select(func.count()).select_from(MusicDownloadHistory).where(
            MusicDownloadHistory.user_id == int(user_id),
            MusicDownloadHistory.downloaded_at >= since)
    )).scalar() or 0)


async def get_cached(session: AsyncSession, cache_key: str) -> MusicCachedFile | None:
    return (await session.execute(
        select(MusicCachedFile).where(MusicCachedFile.cache_key == cache_key)
    )).scalar_one_or_none()


async def record_history(session: AsyncSession, prepared: PreparedTrack, *,
                         user_id: int, job_id: str, track_id: int | None,
                         permission: PermissionStatus) -> MusicDownloadHistory:
    candidate = prepared.candidate
    row = MusicDownloadHistory(
        user_id=int(user_id), track_id=track_id, job_id=job_id,
        provider=candidate.provider, source_url=candidate.source_url or None,
        permission=permission.value, audio_format=prepared.file_format,
        bitrate_kbps=prepared.audio.probe.bitrate_kbps,
        sample_rate=prepared.audio.probe.sample_rate,
        file_size=prepared.size,
        quality_category=prepared.audio.category.value,
        processing_status="completed", media_kind=prepared.media_kind.value,
        cache_key=prepared.cache_key or None,
        title=(candidate.title or "")[:255],
        artist=(candidate.artist or "")[:255],
        duration=int(prepared.duration or 0),
        telegram_file_id=prepared.telegram_file_id,
        telegram_file_unique_id=prepared.telegram_file_unique_id,
    )
    session.add(row)
    await session.flush()
    prepared.history_id = row.id
    return row


async def update_history_telegram(session: AsyncSession, history_id: int,
                                  file_id: str, file_unique_id: str = "") -> None:
    row = await session.get(MusicDownloadHistory, history_id)
    if row:
        row.telegram_file_id = file_id
        row.telegram_file_unique_id = file_unique_id
        await session.flush()


async def record_audio_report(session: AsyncSession, prepared: PreparedTrack,
                              track_id: int | None) -> MusicAudioQualityReport:
    probe = prepared.audio.probe
    row = MusicAudioQualityReport(
        track_id=track_id, history_id=prepared.history_id,
        sha256=prepared.sha256, codec=probe.codec, container=probe.container,
        bitrate_kbps=probe.bitrate_kbps, sample_rate=probe.sample_rate,
        bit_depth=probe.bit_depth, channels=probe.channels,
        duration=probe.duration, size=probe.size,
        category=prepared.audio.category.value, score=prepared.audio.score,
        clipping=prepared.audio.clipping,
        clipped_samples=prepared.audio.clipped_samples,
        silence_total=prepared.audio.silence_total,
        issues=[i.as_dict() for i in prepared.audio.issues])
    session.add(row)
    await session.flush()
    return row


async def record_cover_report(session: AsyncSession, prepared: PreparedTrack,
                              track_id: int | None) -> MusicCoverQualityReport | None:
    cover = prepared.cover
    if cover is None or not cover.candidate.is_valid:
        return None
    row = MusicCoverQualityReport(
        track_id=track_id, history_id=prepared.history_id,
        source_url=cover.candidate.url or None,
        width=cover.candidate.width, height=cover.candidate.height,
        format=cover.candidate.format, size=cover.candidate.size,
        sharpness=cover.sharpness, compression=cover.blockiness,
        is_upscaled=cover.is_upscaled, accepted=cover.accepted,
        issues=[i.as_dict() for i in cover.issues])
    session.add(row)
    await session.flush()
    return row


async def add_favorite(session: AsyncSession, user_id: int, track_id: int) -> bool:
    exists = (await session.execute(
        select(MusicUserFavorite).where(
            MusicUserFavorite.user_id == int(user_id),
            MusicUserFavorite.track_id == int(track_id))
    )).scalar_one_or_none()
    if exists:
        return False
    session.add(MusicUserFavorite(user_id=int(user_id), track_id=int(track_id)))
    await session.flush()
    return True


async def remove_favorite(session: AsyncSession, user_id: int, track_id: int) -> bool:
    row = (await session.execute(
        select(MusicUserFavorite).where(
            MusicUserFavorite.user_id == int(user_id),
            MusicUserFavorite.track_id == int(track_id))
    )).scalar_one_or_none()
    if not row:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def list_favorites(session: AsyncSession, user_id: int, limit: int = 12
                         ) -> list[tuple[MusicUserFavorite, MusicTrack]]:
    rows = (await session.execute(
        select(MusicUserFavorite, MusicTrack)
        .join(MusicTrack, MusicTrack.id == MusicUserFavorite.track_id)
        .where(MusicUserFavorite.user_id == int(user_id))
        .order_by(desc(MusicUserFavorite.created_at)).limit(limit)
    )).all()
    return list(rows)


async def list_history(session: AsyncSession, user_id: int, limit: int = 10
                       ) -> list[MusicDownloadHistory]:
    return list((await session.execute(
        select(MusicDownloadHistory)
        .where(MusicDownloadHistory.user_id == int(user_id))
        .order_by(desc(MusicDownloadHistory.downloaded_at)).limit(limit)
    )).scalars().all())


async def history_for_cache_key(session: AsyncSession, cache_key: str
                                ) -> MusicDownloadHistory | None:
    return (await session.execute(
        select(MusicDownloadHistory)
        .where(MusicDownloadHistory.cache_key == cache_key)
        .order_by(desc(MusicDownloadHistory.downloaded_at)).limit(1)
    )).scalars().first()


async def get_user_settings(session: AsyncSession, user_id: int) -> MusicUserSettings:
    row = await session.get(MusicUserSettings, int(user_id))
    if row is None:
        row = MusicUserSettings(user_id=int(user_id))
        session.add(row)
        await session.flush()
    return row


async def update_user_settings(session: AsyncSession, user_id: int, **fields) -> MusicUserSettings:
    row = await get_user_settings(session, user_id)
    for key in ("auto_text", "default_quality", "send_mode"):
        if key in fields:
            setattr(row, key, fields[key])
    row.updated_at = utcnow()
    await session.flush()
    return row


async def upsert_source(session: AsyncSession, provider: str, display_name: str,
                        capabilities: dict, health: str) -> MusicSource:
    row = (await session.execute(
        select(MusicSource).where(MusicSource.provider == provider)
    )).scalar_one_or_none()
    if row is None:
        row = MusicSource(provider=provider, display_name=display_name,
                          capabilities=capabilities, health=health,
                          last_check_at=utcnow())
        session.add(row)
    else:
        row.display_name = display_name
        row.capabilities = capabilities
        row.health = health
        row.last_check_at = utcnow()
    await session.flush()
    return row


async def record_job_failure(session: AsyncSession, job_id: str, error: str) -> None:
    row = await session.get(MusicDownloadJob, job_id)
    if row:
        row.error = (error or "")[:2000]
        row.status = JobStatus.FAILED.value
        row.finished_at = utcnow()
        if row.started_at is None:
            row.started_at = utcnow()
        await session.flush()


# ---- необязательные креды в БД (шифруются; .env — основной путь) -------------
def _fernet():
    from cryptography.fernet import Fernet
    seed = (music_settings.credential_key or "music-local").encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(key)


async def set_credential(session: AsyncSession, provider: str, key_name: str,
                         value: str, *, is_secret: bool = True) -> None:
    from .models import MusicApiCredential
    encrypted = _fernet().encrypt(value.encode()).decode()
    row = (await session.execute(
        select(MusicApiCredential).where(MusicApiCredential.provider == provider,
                                         MusicApiCredential.key_name == key_name)
    )).scalar_one_or_none()
    if row is None:
        row = MusicApiCredential(provider=provider, key_name=key_name,
                                 encrypted_value=encrypted, is_secret=is_secret)
        session.add(row)
    else:
        row.encrypted_value = encrypted
        row.is_secret = is_secret
    await session.flush()


async def get_credential(session: AsyncSession, provider: str, key_name: str) -> str:
    from .models import MusicApiCredential
    row = (await session.execute(
        select(MusicApiCredential).where(MusicApiCredential.provider == provider,
                                         MusicApiCredential.key_name == key_name)
    )).scalar_one_or_none()
    if not row:
        return ""
    try:
        return _fernet().decrypt(row.encrypted_value.encode()).decode()
    except Exception as exc:  # noqa: BLE001
        log.warning("credential decrypt %s/%s: %s", provider, key_name, exc)
        return ""


__all__ = [
    "ensure_user", "upsert_track", "record_job", "update_job", "count_active_jobs",
    "count_daily_downloads", "get_cached", "record_history",
    "update_history_telegram", "record_audio_report", "record_cover_report",
    "add_favorite", "remove_favorite", "list_favorites", "list_history",
    "history_for_cache_key", "get_user_settings", "update_user_settings",
    "upsert_source", "record_job_failure", "set_credential", "get_credential",
]
