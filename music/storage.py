"""Временные файлы, изоляция каталогов, диск, хеши, очистка."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from .config import music_settings
from .errors import NoSpaceError, ProcessingError
from .net import sanitize_filename

log = logging.getLogger("music.storage")

_JOB_RE = re.compile(r"^[a-f0-9-]{8,64}$")
_MIN_FREE_BYTES = 64 * 1024 * 1024


def new_job_id() -> str:
    return uuid.uuid4().hex


def workspace_for(job_id: str) -> Path:
    if not _JOB_RE.match(job_id or ""):
        raise ProcessingError("Некорректный job id", code="BAD_JOB_ID")
    path = music_settings.tmp_dir / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_path(base: Path, name: str) -> Path:
    """Путь внутри base; любые попытки выйти наружу отклоняются."""
    candidate = (Path(base) / sanitize_filename(name)).resolve()
    root = Path(base).resolve()
    if root != candidate and root not in candidate.parents:
        raise ProcessingError("Path traversal заблокирован", code="PATH_TRAVERSAL")
    return candidate


def temp_file(job_id: str, suffix: str = ".bin", stem: str = "audio") -> Path:
    stem = sanitize_filename(stem, fallback="audio", max_len=80) or "audio"
    if not suffix.startswith("."):
        suffix = "." + suffix
    suffix = re.sub(r"[^A-Za-z0-9.]", "", suffix)[:10] or ".bin"
    return workspace_for(job_id) / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


def check_disk_space(required_bytes: int = 0, base: Path | None = None) -> int:
    path = base or music_settings.data_dir
    try:
        free = shutil.disk_usage(path).free
    except OSError as exc:
        raise NoSpaceError(f"Не удалось проверить диск: {exc}") from exc
    need = max(int(required_bytes), _MIN_FREE_BYTES)
    if free < need:
        raise NoSpaceError(f"Недостаточно места: свободно {free // (1024 * 1024)} МБ")
    return free


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_size(path: Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def cleanup_job_dir(job_id: str) -> None:
    path = music_settings.tmp_dir / job_id
    shutil.rmtree(path, ignore_errors=True)


def cleanup_temp(ttl_hours: int | None = None) -> int:
    """Удаляет старые рабочие каталоги и .part-файлы. Возвращает число удалённых."""
    ttl = (ttl_hours if ttl_hours is not None else music_settings.temp_ttl_hours) * 3600
    cutoff = time.time() - ttl
    removed = 0
    root = music_settings.tmp_dir
    if not root.exists():
        return 0
    for entry in root.iterdir():
        try:
            mtime = entry.stat().st_mtime
            if mtime >= cutoff:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            log.debug("cleanup_temp: %s: %s", entry, exc)
    return removed


def quarantine(path: Path, job_id: str) -> Path | None:
    """Повреждённый файл убираем из кэша (для разбора, не для отправки)."""
    try:
        dest = music_settings.quarantine_dir / f"{job_id}-{sanitize_filename(Path(path).name)}"
        Path(path).replace(dest)
        return dest
    except OSError as exc:
        log.warning("quarantine: %s", exc)
        return None


def guess_extension(content_type: str, url: str = "", fallback: str = ".bin") -> str:
    ctype = (content_type or "").lower()
    mapping = {
        "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mpeg3": ".mp3",
        "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac",
        "audio/flac": ".flac", "audio/x-flac": ".flac",
        "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
        "audio/ogg": ".ogg", "application/ogg": ".ogg",
        "audio/opus": ".opus", "audio/webm": ".webm",
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    }
    if ctype in mapping:
        return mapping[ctype]
    suffix = Path((url or "").split("?")[0]).suffix.lower()
    if suffix in (".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus",
                  ".webm", ".jpg", ".jpeg", ".png", ".webp"):
        return ".jpg" if suffix == ".jpeg" else suffix
    return fallback


def detect_audio_extension(path: Path, content_type: str = "") -> str:
    """Сигнатура важнее Content-Type."""
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(64)
    except OSError:
        head = b""
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if head.startswith(b"fLaC"):
        return ".flac"
    if head.startswith(b"OggS"):
        # OpusHead лежит в первом Ogg-пакете (смещение ~28), поэтому читаем 64 байта
        return ".opus" if b"OpusHead" in head else ".ogg"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return ".wav"
    if head.startswith(b"ftyp") or head[4:8] == b"ftyp":
        return ".m4a"
    return guess_extension(content_type, "", ".bin")


def ensure_not_executable(name: str) -> str:
    """Скачанные файлы не должны исполняться: опасные расширения меняем."""
    bad = {".exe", ".dll", ".scr", ".bat", ".cmd", ".ps1", ".sh", ".py",
           ".js", ".vbs", ".msi", ".jar", ".com", ".apk"}
    stem, ext = os.path.splitext(name)
    if ext.lower() in bad:
        return stem + ".bin"
    return name


__all__ = [
    "new_job_id", "workspace_for", "safe_path", "temp_file", "check_disk_space",
    "sha256_file", "sha256_bytes", "file_size", "cleanup_job_dir", "cleanup_temp",
    "quarantine", "guess_extension", "detect_audio_extension", "ensure_not_executable",
]
