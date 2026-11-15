"""Настройки музыкальной системы из `.env` (префикс MUSIC_*).

Секреты читаются только из окружения. `music_credentials` в БД хранит
необязательные переопределения в зашифрованном виде, но .env — основной путь.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core.config import settings as core_settings


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)) or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    raw = _env(name)
    return [x.strip().lower() for x in raw.replace(";", ",").split(",") if x.strip()]


def _env_any(*names: str, default: str = "") -> str:
    """Первое непустое значение из списка имён (поддержка алиасов env)."""
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return default


def _float_any(default: float, *names: str) -> float:
    try:
        return float(_env_any(*names, default=str(default)) or default)
    except ValueError:
        return default


@dataclass(slots=True)
class MusicSettings:
    enabled: bool = field(default_factory=lambda: _bool("MUSIC_ENABLED", True))
    auto_text: bool = field(default_factory=lambda: _bool("MUSIC_AUTO_TEXT", False))

    data_dir: Path = field(default_factory=lambda: Path(
        _env("MUSIC_DATA_DIR") or (core_settings.DATA_DIR / "music")))
    max_file_mb: int = field(default_factory=lambda: _int("MUSIC_MAX_FILE_MB", 200))
    max_cover_mb: int = field(default_factory=lambda: _int("MUSIC_MAX_COVER_MB", 15))
    max_duration_sec: int = field(default_factory=lambda: _int("MUSIC_MAX_DURATION_SEC", 3 * 3600))
    min_duration_sec: int = field(default_factory=lambda: _int("MUSIC_MIN_DURATION_SEC", 8))

    search_results: int = field(default_factory=lambda: _int("MUSIC_RESULTS_LIMIT", 12))
    provider_results: int = field(default_factory=lambda: _int("MUSIC_PROVIDER_RESULTS", 8))
    search_timeout: float = field(default_factory=lambda: _float("MUSIC_SEARCH_TIMEOUT", 12.0))
    provider_timeout: float = field(default_factory=lambda: _float("MUSIC_PROVIDER_TIMEOUT", 10.0))
    download_timeout: float = field(default_factory=lambda: _float("MUSIC_DOWNLOAD_TIMEOUT", 180.0))
    connect_timeout: float = field(default_factory=lambda: _float("MUSIC_CONNECT_TIMEOUT", 10.0))

    workers: int = field(default_factory=lambda: max(1, _int("MUSIC_WORKERS", 3)))
    user_active_jobs: int = field(default_factory=lambda: max(1, _int("MUSIC_USER_ACTIVE_JOBS", 2)))
    user_daily_jobs: int = field(default_factory=lambda: max(0, _int("MUSIC_USER_DAILY_JOBS", 60)))
    job_timeout: float = field(default_factory=lambda: _float("MUSIC_JOB_TIMEOUT", 420.0))
    max_attempts: int = field(default_factory=lambda: max(1, _int("MUSIC_JOB_ATTEMPTS", 3)))
    retry_backoff: float = field(default_factory=lambda: _float("MUSIC_RETRY_BACKOFF", 2.0))

    temp_ttl_hours: int = field(default_factory=lambda: _int("MUSIC_TEMP_TTL_HOURS", 6))
    cache_ttl_days: int = field(default_factory=lambda: _int("MUSIC_CACHE_TTL_DAYS", 14))
    cache_max_mb: int = field(default_factory=lambda: _int("MUSIC_CACHE_MAX_MB", 4096))
    rate_per_minute: int = field(default_factory=lambda: _int("MUSIC_RATE_PER_MINUTE", 20))
    download_rate_mbps: float = field(default_factory=lambda: _float("MUSIC_DOWNLOAD_RATE_MBPS", 0.0))

    jamendo_client_id: str = field(default_factory=lambda: _env("JAMENDO_CLIENT_ID") or _env("MUSIC_JAMENDO_CLIENT_ID"))
    archive_enabled: bool = field(default_factory=lambda: _bool("MUSIC_ARCHIVE_ENABLED", True))
    soundcloud_client_id: str = field(default_factory=lambda: _env("SOUNDCLOUD_CLIENT_ID") or _env("MUSIC_SOUNDCLOUD_CLIENT_ID"))
    soundcloud_client_secret: str = field(default_factory=lambda: _env("SOUNDCLOUD_CLIENT_SECRET") or _env("MUSIC_SOUNDCLOUD_CLIENT_SECRET"))
    spotify_client_id: str = field(default_factory=lambda: _env("SPOTIFY_CLIENT_ID"))
    spotify_client_secret: str = field(default_factory=lambda: _env("SPOTIFY_CLIENT_SECRET"))
    bandcamp_enabled: bool = field(default_factory=lambda: _bool("MUSIC_BANDCAMP_ENABLED", True))

    proxy_url: str = field(default_factory=lambda: _env("MUSIC_PROXY_URL") or core_settings.PROXY_URL)
    allow_hosts: list[str] = field(default_factory=lambda: _list("MUSIC_EXTRA_ALLOWED_HOSTS"))
    block_private_ips: bool = field(default_factory=lambda: _bool("MUSIC_BLOCK_PRIVATE_IPS", True))
    allowed_ports: tuple[int, ...] = field(default_factory=lambda: (80, 443))

    max_concurrent_downloads_per_host: int = field(default_factory=lambda: _int("MUSIC_HOST_CONCURRENCY", 2))
    ffmpeg_bin: str = field(default_factory=lambda: _env("FFMPEG_BIN") or "ffmpeg")
    ffprobe_bin: str = field(default_factory=lambda: _env("FFPROBE_BIN") or "ffprobe")
    ffmpeg_timeout: float = field(default_factory=lambda: _float("MUSIC_FFMPEG_TIMEOUT", 180.0))

    # отправка
    local_bot_api: bool = field(default_factory=lambda: bool(core_settings.BOT_API_URL))
    max_upload_mb: int = field(default_factory=lambda: _int(
        "MUSIC_MAX_UPLOAD_MB", 2000 if core_settings.BOT_API_URL else 50))
    audio_caption_limit: int = 1024
    document_caption_limit: int = 1024

    # --- распределённые роли серверов (тянущая очередь скачиваний в Neon) ---
    # local: всё в одном процессе (без env — прежнее поведение);
    # app: бот+мини-апп+стрим, скачивание уезжает в очередь media_download_jobs
    #      (локальный воркер Render — последний резерв);
    # worker: только скачивания (run_worker.py на Botkeep/serv00), без поллинга ТГ.
    media_role: str = field(default_factory=lambda: _env("MEDIA_ROLE", "local").lower())
    worker_name: str = field(default_factory=lambda: _env("MEDIA_WORKER_NAME", "local"))
    # Приоритет воркеров настраивается интервалом опроса: Botkeep 3–5 с
    # успевает раньше serv00 20–30 с — «запасной при загруженности» без роутинга.
    worker_poll_sec: float = field(
        default_factory=lambda: _float_any(3.0, "MEDIA_POLL_SEC", "MUSIC_POLL_SEC"))
    local_poll_sec: float = field(
        default_factory=lambda: _float_any(60.0, "MEDIA_LOCAL_POLL_SEC"))
    lease_ttl_sec: int = field(default_factory=lambda: _int("MEDIA_LEASE_TTL_SEC", 900))
    local_fallback_sec: int = field(
        default_factory=lambda: _int("MEDIA_LOCAL_FALLBACK_SEC", 90))
    worker_caps_ffmpeg: bool = field(default_factory=lambda: _bool("MEDIA_CAPS_FFMPEG", True))
    media_max_attempts: int = field(default_factory=lambda: max(1, _int("MEDIA_MAX_ATTEMPTS", 3)))
    # Жёсткий потолок одной джобы (скачивание+доставка). Лечит вечные
    # зависания в фазе, где таймеров нет (Telegram-upload большого файла),
    # не трогая нормальные загрузки (десятки секунд — единицы минут).
    job_timeout_sec: int = field(default_factory=lambda: _int("MEDIA_JOB_TIMEOUT_SEC", 2400))

    # шифрование необязательных кредов в БД
    credential_key: str = field(default_factory=lambda: _env("MUSIC_CREDENTIAL_KEY") or core_settings.BOT_TOKEN)

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def quarantine_dir(self) -> Path:
        return self.data_dir / "quarantine"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.tmp_dir, self.cache_dir,
                  self.covers_dir, self.quarantine_dir):
            d.mkdir(parents=True, exist_ok=True)


music_settings = MusicSettings()
music_settings.ensure_dirs()
