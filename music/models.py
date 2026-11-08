"""ORM-модели музыкальной системы: отдельный `MusicBase` и префикс `music_*`."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, JSON, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC: SQLite и asyncpg ожидают одинаковый формат."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MusicBase(DeclarativeBase):
    pass


class MusicUser(MusicBase):
    __tablename__ = "music_users"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MusicUserSettings(MusicBase):
    __tablename__ = "music_user_settings"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    auto_text: Mapped[bool] = mapped_column(Boolean, default=False)
    default_quality: Mapped[str] = mapped_column(String(24), default="original")
    send_mode: Mapped[str] = mapped_column(String(16), default="auto")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MusicArtist(MusicBase):
    __tablename__ = "music_artists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    albums: Mapped[list["MusicAlbum"]] = relationship(back_populates="artist")
    __table_args__ = (UniqueConstraint("normalized", name="uq_music_artist_norm"),)


class MusicAlbum(MusicBase):
    __tablename__ = "music_albums"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    normalized: Mapped[str] = mapped_column(String(255), index=True)
    artist_id: Mapped[int | None] = mapped_column(ForeignKey("music_artists.id"))
    year: Mapped[int | None] = mapped_column(Integer)
    cover_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    artist: Mapped[MusicArtist | None] = relationship(back_populates="albums")
    __table_args__ = (
        UniqueConstraint("normalized", "artist_id", name="uq_music_album_norm_artist"),
    )


class MusicTrack(MusicBase):
    __tablename__ = "music_tracks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    artist_id: Mapped[int | None] = mapped_column(ForeignKey("music_artists.id"))
    album_id: Mapped[int | None] = mapped_column(ForeignKey("music_albums.id"))
    artist_name: Mapped[str] = mapped_column(String(255), default="")
    album_name: Mapped[str] = mapped_column(String(255), default="")
    duration: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(24), default="original")
    isrc: Mapped[str | None] = mapped_column(String(32), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text)
    license_name: Mapped[str | None] = mapped_column(String(128))
    license_url: Mapped[str | None] = mapped_column(Text)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_key: Mapped[str] = mapped_column(String(512), index=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    artist: Mapped[MusicArtist | None] = relationship()
    album: Mapped[MusicAlbum | None] = relationship()
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_music_track_provider_ext"),
    )


class MusicSource(MusicBase):
    __tablename__ = "music_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    health: Mapped[str] = mapped_column(String(16), default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)


class MusicDownloadJob(MusicBase):
    __tablename__ = "music_download_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    external_id: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    permission: Mapped[str] = mapped_column(String(32), default="LICENSE_UNKNOWN")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_music_jobs_user_status", "user_id", "status"),)


class MusicDownloadHistory(MusicBase):
    __tablename__ = "music_download_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("music_tracks.id"))
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    source_url: Mapped[str | None] = mapped_column(Text)
    permission: Mapped[str] = mapped_column(String(32), default="")
    audio_format: Mapped[str] = mapped_column(String(16), default="")
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    quality_category: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    processing_status: Mapped[str] = mapped_column(String(24), default="completed")
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    media_kind: Mapped[str] = mapped_column(String(16), default="audio")
    cache_key: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    artist: Mapped[str] = mapped_column(String(255), default="")
    duration: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class MusicAudioQualityReport(MusicBase):
    __tablename__ = "music_audio_quality_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("music_tracks.id"))
    history_id: Mapped[int | None] = mapped_column(ForeignKey("music_download_history.id"))
    sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    codec: Mapped[str] = mapped_column(String(32), default="")
    container: Mapped[str] = mapped_column(String(32), default="")
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    bit_depth: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float] = mapped_column(Float, default=0)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    category: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    score: Mapped[int] = mapped_column(Integer, default=0)
    clipping: Mapped[bool] = mapped_column(Boolean, default=False)
    clipped_samples: Mapped[int] = mapped_column(Integer, default=0)
    silence_total: Mapped[float] = mapped_column(Float, default=0)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MusicCoverQualityReport(MusicBase):
    __tablename__ = "music_cover_quality_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("music_tracks.id"))
    history_id: Mapped[int | None] = mapped_column(ForeignKey("music_download_history.id"))
    source_url: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    format: Mapped[str] = mapped_column(String(16), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sharpness: Mapped[float] = mapped_column(Float, default=0)
    compression: Mapped[float] = mapped_column(Float, default=0)
    is_upscaled: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MusicCachedFile(MusicBase):
    __tablename__ = "music_cached_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(128), unique=True)
    identity_key: Mapped[str] = mapped_column(String(512), index=True, default="")
    path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True, default="")
    format: Mapped[str] = mapped_column(String(16), default="")
    duration: Mapped[float] = mapped_column(Float, default=0)
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    media_kind: Mapped[str] = mapped_column(String(16), default="audio")
    quality_category: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255), default="")
    artist: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class MusicUserFavorite(MusicBase):
    __tablename__ = "music_user_favorites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("music_tracks.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        UniqueConstraint("user_id", "track_id", name="uq_music_fav_user_track"),
    )


class MusicApiCredential(MusicBase):
    __tablename__ = "music_api_credentials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32))
    key_name: Mapped[str] = mapped_column(String(64))
    encrypted_value: Mapped[str] = mapped_column(Text)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (
        UniqueConstraint("provider", "key_name", name="uq_music_cred_provider_key"),
    )


__all__ = [
    "MusicBase", "MusicUser", "MusicUserSettings", "MusicArtist", "MusicAlbum",
    "MusicTrack", "MusicSource", "MusicDownloadJob", "MusicDownloadHistory",
    "MusicAudioQualityReport", "MusicCoverQualityReport", "MusicCachedFile",
    "MusicUserFavorite", "MusicApiCredential", "utcnow",
]
