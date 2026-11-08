"""Проверка скачанного файла: размер, сигнатура, реальный аудиопоток."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..config import music_settings
from ..domain import AudioProbe
from ..errors import FileTooLarge, InvalidContent, ProcessingError
from ..quality.analyzer import probe_file
from ..storage import detect_audio_extension, ensure_not_executable, sanitize_filename

log = logging.getLogger("music.file_handler")

_ALLOWED_CODECS = {"mp3", "mp2", "aac", "alac", "flac", "vorbis", "opus",
                   "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le",
                   "pcm_u8", "wavpack", "aiff"}


def validate_content_type(content_type: str) -> None:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if not ctype:
        return
    if ctype.startswith(("audio/", "video/mp4", "application/octet-stream",
                         "binary/octet-stream")):
        return
    raise InvalidContent(f"Content-Type {ctype} не является аудио", code="BAD_CONTENT_TYPE")


async def finalize(path: str | Path, *, content_type: str = "",
                   expected_format: str = "", job_id: str = "") -> tuple[Path, AudioProbe]:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise ProcessingError("Скачанный файл отсутствует", code="FILE_MISSING")
    size = p.stat().st_size
    limit = music_settings.max_file_mb * 1024 * 1024
    if size <= 0:
        raise InvalidContent("Пустой файл", code="EMPTY_FILE")
    if size > limit:
        raise FileTooLarge(f"Файл {size // (1024 * 1024)} МБ больше лимита "
                           f"{music_settings.max_file_mb} МБ", code="FILE_TOO_LARGE")
    validate_content_type(content_type)
    probe = await probe_file(p)
    if not probe.has_audio:
        raise InvalidContent("В файле нет аудиопотока (возможно, это не аудио)",
                             code="NO_AUDIO")
    if probe.duration <= 0:
        raise InvalidContent("Не удалось прочитать длительность", code="BAD_DURATION")
    if probe.duration < music_settings.min_duration_sec:
        raise InvalidContent(
            f"Слишком короткий файл: {probe.duration:.1f}с", code="TOO_SHORT")
    if probe.duration > music_settings.max_duration_sec:
        raise FileTooLarge(
            f"Слишком длинный файл: {int(probe.duration)}с", code="TOO_LONG")
    codec = (probe.codec or "").lower()
    if codec and codec not in _ALLOWED_CODECS:
        log.info("file_handler: нестандартный кодек %s (%s)", codec, p.name)

    detected = detect_audio_extension(p, content_type)
    if detected == ".bin":
        detected = _ext_from_probe(probe)
    actual_ext = p.suffix.lower()
    if detected and detected != actual_ext:
        stem = sanitize_filename(p.stem, fallback="audio", max_len=100)
        new_path = p.with_name(ensure_not_executable(stem + detected))
        if new_path.exists():
            new_path = p.with_name(f"{stem}-{job_id[:6]}{detected}")
        try:
            p.replace(new_path)
            log.info("file_handler: %s → %s (реальный формат)", actual_ext, detected)
            p = new_path
        except OSError as exc:
            log.debug("file_handler rename: %s", exc)
    if expected_format and detected:
        expected_ext = "." + expected_format.lstrip(".").lower()
        if expected_ext != detected and expected_ext not in (".m4a", ".mp4"):
            log.info("file_handler: ожидался %s, получен %s", expected_ext, detected)
    return p, probe


def _ext_from_probe(probe: AudioProbe) -> str:
    codec = (probe.codec or "").lower()
    container = (probe.container or "").lower()
    if codec in ("mp3", "mp2"):
        return ".mp3"
    if codec == "aac":
        return ".m4a" if container in ("m4a", "mp4") else ".aac"
    if codec == "alac":
        return ".m4a"
    if codec == "flac":
        return ".flac"
    if codec.startswith("pcm"):
        return ".wav"
    if codec == "opus":
        return ".opus"
    if codec == "vorbis":
        return ".ogg"
    return f".{container}" if container in ("mp3", "flac", "wav", "ogg", "m4a") else ".bin"


def copy_local(src: str | Path, dest_dir: Path, *, job_id: str) -> Path:
    source = Path(src)
    if not source.exists():
        raise ProcessingError("Загруженный файл не найден", code="USER_FILE_MISSING")
    name = sanitize_filename(source.name, fallback=f"upload-{job_id[:8]}", max_len=100)
    dest = Path(dest_dir) / ensure_not_executable(name)
    if dest.exists():
        dest = Path(dest_dir) / f"{job_id[:8]}-{dest.name}"
    shutil.copy2(source, dest)
    return dest


__all__ = ["validate_content_type", "finalize", "copy_local"]
