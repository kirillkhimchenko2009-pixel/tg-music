"""ORM-модели: пользователи, треки-каталог, плейлисты, токены сервисов."""
from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, JSON, SmallInteger, String, Text,
                        UniqueConstraint, func)
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
    censored: Mapped[bool] = mapped_column(Boolean, default=False)  # детектор: заливка с мутами/бипами — не отдаём
    cover_url: Mapped[str | None] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(Text)         # официальная страница композиции/источника
    license: Mapped[str | None] = mapped_column(String(64))    # тип лицензии (CC-BY и т.п.), если известен
    quality: Mapped[str | None] = mapped_column(String(12))    # flac | aac320 | ...
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_id: Mapped[str | None] = mapped_column(String(256))   # кеш Telegram file_id
    local_path: Mapped[str | None] = mapped_column(Text)       # путь файла в data/audio
    # Аудио-анализ (services/audio_features.py): реальные признаки звука для
    # настроений волны. analyzed_at NULL — трек ещё не слышали.
    bpm: Mapped[float] = mapped_column(Float, default=0.0)
    energy: Mapped[float] = mapped_column(Float, default=0.0)       # 0..1 громкость/плотность
    valence: Mapped[float] = mapped_column(Float, default=0.0)      # 0..1 грусть↔веселье
    danceability: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1 бит/танцевальность
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime)
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


# ----------------------------- волна («Моя волна») -----------------------------

class WaveSettings(Base):
    """Формула волны юзера: аналог настроек «Мой волны» ЯМ.

    JSON-поля (не JSONB) — формула должна одинаково жить и в Postgres, и в SQLite.
    """
    __tablename__ = "wave_settings"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    engine: Mapped[str] = mapped_column(String(16), default="auto")     # auto | yandex | own
    mood: Mapped[str | None] = mapped_column(String(16))                # fun | active | calm | sad | edge
    diversity: Mapped[str] = mapped_column(String(16), default="default")  # favorite | popular | discover | default
    language: Mapped[str] = mapped_column(String(16), default="any")    # any | ru | non-ru
    activities: Mapped[list] = mapped_column(JSON, default=list)        # ["work","sport","road",...]
    genres: Mapped[list] = mapped_column(JSON, default=list)            # ["hiphop","rock",...]
    artists: Mapped[list] = mapped_column(JSON, default=list)           # [{"name":"Хаски","ym_id":393663}, ...]
    allow_explicit: Mapped[bool] = mapped_column(Boolean, default=True)  # 18+
    context_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # контекстная персонализация (§20)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WaveSession(Base):
    """Сессия волны: одна активная на юзера; rotor-часть — это движок A (ЯМ)."""
    __tablename__ = "wave_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    engine: Mapped[str] = mapped_column(String(16))                     # yandex | own
    seeds: Mapped[list] = mapped_column(JSON, default=list)             # сиды rotor / формула сессии
    radio_session_id: Mapped[str | None] = mapped_column(Text)          # id сессии rotor (движок A)
    batch_id: Mapped[str | None] = mapped_column(Text)                  # id текущей партии
    played_ids: Mapped[list] = mapped_column(JSON, default=list)        # external_id (ЯМ) отгруженных треков
    played_track_ids: Mapped[list] = mapped_column(JSON, default=list)  # локальные track_id анти-повтора
    prefetch: Mapped[list] = mapped_column(JSON, default=list)          # заряженная следующая партия (DTO)
    scenario: Mapped[dict] = mapped_column(JSON, default=dict)          # активный сценарий {id,title,desc,palette,...}
    context: Mapped[dict] = mapped_column(JSON, default=dict)           # контекст сессии (время/занятие/настроение/устройство)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlayEvent(Base):
    """Факт прослушивания: история юзера (ядро вкуса для движка B)."""
    __tablename__ = "play_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("wave_sessions.id"))
    origin: Mapped[str] = mapped_column(String(16), default="wave")     # wave | search | playlist | library
    event_id: Mapped[str | None] = mapped_column(String(48))           # идемпотентность офлайн-событий (§17)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    played_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)       # next до конца (<25 c = жёсткий скип)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_play_events_user_time", "user_id", "started_at"),
                      Index("ix_play_events_user_event", "user_id", "event_id",
                            unique=True),)


class WaveEvent(Base):
    """Гранулярные события волны/плеера (телеметрия рекомендательного движка).

    kind: impression | track_started | played_10s | played_30s | played_50p |
          played_90p | completed | skipped | replayed | seeked | liked |
          unliked | disliked | added_to_library | removed_from_library |
          artist_opened | search_selected | context_selected |
          wave_refreshed | wave_shaken
    reason (для disliked): composition | artist | genre | repeat | mood |
          too_energy | too_calm; (для skipped): skip класс (позиция скипа).
    context: {time_of_day, weekday, device, activity?, mood?} — без геолокации.
    """
    __tablename__ = "wave_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id"), index=True)
    artist_key: Mapped[str | None] = mapped_column(String(128), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("wave_sessions.id"))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str | None] = mapped_column(String(24))
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    scenario: Mapped[str | None] = mapped_column(String(64))
    played_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    track_duration: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        Index("ix_wave_events_user_time", "user_id", "created_at"),
    )


class TasteProfile(Base):
    """LONG-TERM профиль вкуса. Обновляется фоново после значимых событий.

    SESSION TASTE сюда НЕ попадает: он считается из активной сессии.
    context_affinity — устойчивые контекстные привычки (занятие → жанр/настроение).
    """
    __tablename__ = "taste_profiles"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    artist_affinity: Mapped[dict] = mapped_column(JSON, default=dict)   # {artist_key: {score, plays, likes, last_at}}
    genre_affinity: Mapped[dict] = mapped_column(JSON, default=dict)    # {genre: score}
    mood_affinity: Mapped[dict] = mapped_column(JSON, default=dict)     # {mood: score}
    language_affinity: Mapped[dict] = mapped_column(JSON, default=dict) # {"ru": score, "non_ru": score}
    context_affinity: Mapped[dict] = mapped_column(JSON, default=dict)  # {activity: {"genres": {}, "moods": {}}}
    explore_share: Mapped[float] = mapped_column(Float, default=0.20)   # адаптивная доля открытий
    excluded_artists: Mapped[list] = mapped_column(JSON, default=list)  # ["artist_key"]
    excluded_genres: Mapped[list] = mapped_column(JSON, default=list)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ArtistStats(Base):
    """Интерес пользователя к артисту + discovery-память («когда открыл»)."""
    __tablename__ = "artist_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    artist_key: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    plays: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    dislikes: Mapped[int] = mapped_column(Integer, default=0)
    skips: Mapped[int] = mapped_column(Integer, default=0)
    completions: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime)   # первое появление в рекомендации
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime)
    discovered_via: Mapped[str | None] = mapped_column(String(24))     # discovery | library | formula | search
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("user_id", "artist_key", name="uq_artist_stats_user"),
        Index("ix_artist_stats_user_plays", "user_id", "plays"),
    )


class WaveKV(Base):
    """Конфигурация рекомендательного движка отдельно от кода (веса, лимиты,
    editorial-пул). value — JSON; ключи читает services/wave/intel/config.py."""
    __tablename__ = "wave_kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | int | float | str] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TrackFeedback(Base):
    """Прямой сигнал по треку в волне. Лайки живёт в «❤ Любимое», тут дисклайки.

    kind: -1 дисклайк (мораторий на трек/артиста), +1 лайк (резерв)."""
    __tablename__ = "track_feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), index=True)
    kind: Mapped[int] = mapped_column(SmallInteger)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        UniqueConstraint("user_id", "track_id", "kind", name="uq_track_feedback_kind"),
        Index("ix_track_feedback_user_track", "user_id", "track_id"),
    )


class Subscription(Base):
    """Подписка Vibe (оплата Telegram-звёздами, currency XTR).
    Два тира: lite и pro — у каждого своя строка на пользователя.
    При продлении expires_at сдвигается от максимума(now, expires_at)."""
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tier: Mapped[str] = mapped_column(String(16), default="pro")      # lite | pro
    plan: Mapped[str] = mapped_column(String(16), default="month")    # срок: month | half | year
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | expired | refunded
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    __table_args__ = (
        UniqueConstraint("user_id", "tier", name="uq_sub_user_tier"),
    )


class StarPayment(Base):
    """Журнал платежей звёздами (successful_payment из Bot API).
    telegram_payment_charge_id уникален — защита от двойного зачисления."""
    __tablename__ = "star_payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tier: Mapped[str] = mapped_column(String(16), default="pro")
    plan: Mapped[str] = mapped_column(String(16))                     # срок
    amount: Mapped[int] = mapped_column(Integer)          # звёзды
    charge_id: Mapped[str] = mapped_column(String(128), unique=True)
    payload: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
