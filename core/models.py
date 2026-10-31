"""ORM-модели: пользователи, треки-каталог, плейлисты, токены сервисов."""
from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

def utcnow() -> datetime:
    """Naive UTC: SQLite хранит так, и asyncpg требует naive для
    timestamp-колонок Postgres (timezone-aware он отвергает)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram_id
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(64), default="")
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    playlists: Mapped[list["Playlist"]] = relationship(back_populates="owner")

class Track(Base):
    """Каталог трека. Дубликатов нет: (source, external_id) уникальны,
    плюс текстовый индекс по названию/исполнителю для быстрого локального поиска."""
    __tablename__ = "tracks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(24))            # ytmusic / yandex / zvuk
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(256))
    artist: Mapped[str] = mapped_column(String(256), default="")
    album: Mapped[str | None] = mapped_column(String(256))
    year: Mapped[int | None] = mapped_column(Integer)
    isrc: Mapped[str | None] = mapped_column(String(16), index=True)
    duration: Mapped[int] = mapped_column(Integer, default=0)  # сек
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)  # 18+: у ЯМ бывают «чистые» фонограммы
    cover_url: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[str | None] = mapped_column(String(12))    # flac | aac320 | ...
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_id: Mapped[str | None] = mapped_column(String(256))   # кеш Telegram file_id
    local_path: Mapped[str | None] = mapped_column(Text)       # путь файла в data/audio
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_track_source"),
        Index("ix_tracks_search", "artist", "title"),
    )

class Playlist(Base):
    __tablename__ = "playlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), default="")   # "" | manual | import
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    owner: Mapped["User"] = relationship(back_populates="playlists")
    items: Mapped[list["PlaylistTrack"]] = relationship(
        back_populates="playlist", cascade="all, delete-orphan",
        order_by="PlaylistTrack.position")

class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id"), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    playlist: Mapped["Playlist"] = relationship(back_populates="items")
    track: Mapped["Track"] = relationship()

class SourceAuth(Base):
    __tablename__ = "source_auths"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    service: Mapped[str] = mapped_column(String(24))           # ym / zvuk / spotify
    token: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class AuthCode(Base):
    """Одноразовый код для входа в веб-приложение из обычного браузера
    (там недоступно initData Telegram). Выдаётся командой /code."""
    __tablename__ = "auth_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    used: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class DownloadJob(Base):
    """Очередь загрузок для обработки в фоне."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id"))
    payload: Mapped[str] = mapped_column(Text, default="{}")   # json
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|done|failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())
