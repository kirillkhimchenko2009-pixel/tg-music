"""Аккуратная однократная конвертация (только по явному запросу).

По умолчанию пайплайн ничего не перекодирует: MP3/M4A уходят как есть,
FLAC/WAV/OGG/OPUS — документом. Конвертер нужен для будущих сценариев и
сохраняет частоту дискретизации/каналы, не меняет темп и высоту тона.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import music_settings
from ..domain import AudioProbe, MediaKind
from ..enums import MediaKind as MediaKindEnum
from ..errors import ProcessingError
from ..ffmpeg import ffmpeg_path, run_process
from ..quality.analyzer import probe_file
from ..storage import sanitize_filename

log = logging.getLogger("music.processing.converter")

_AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".webm"}
_TELEGRAM_AUDIO = {"mp3", "m4a"}
_MIME = {
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac",
    "flac": "audio/flac", "wav": "audio/wav", "ogg": "audio/ogg",
    "opus": "audio/opus", "webm": "audio/webm",
}


def audio_format(path: str | Path, codec: str = "") -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    if ext == "m4a" or codec in ("aac", "alac"):
        return "m4a"
    if ext in ("mp3", "mpeg"):
        return "mp3"
    if ext in ("ogg", "oga"):
        return "opus" if codec == "opus" else "ogg"
    if ext in _AUDIO_EXT:
        return ext
    return codec or "unknown"


def is_telegram_audio(fmt: str) -> bool:
    return (fmt or "").lower() in _TELEGRAM_AUDIO


def media_kind_for(fmt: str) -> MediaKindEnum:
    return MediaKindEnum.AUDIO if is_telegram_audio(fmt) else MediaKindEnum.DOCUMENT


def mime_for(fmt: str) -> str:
    return _MIME.get((fmt or "").lower(), "application/octet-stream")


def telegram_filename(artist: str, title: str, fmt: str) -> str:
    base = " - ".join(x for x in (artist.strip(), title.strip()) if x).strip(" -")
    base = sanitize_filename(base or "audio", fallback="audio", max_len=80)
    return f"{base}.{fmt.lstrip('.')}" if fmt else base


async def convert(src: str | Path, dest: str | Path, target: str, *,
                  bitrate: int | None = None, timeout: float | None = None) -> AudioProbe:
    """Одна конвертация в mp3/m4a/flac без эффектов и изменения темпа."""
    source = Path(src)
    output = Path(dest)
    target = target.lower().strip(".")
    if target not in ("mp3", "m4a", "flac"):
        raise ProcessingError(f"Целевой формат {target} не поддерживается",
                              code="CONVERT_TARGET")
    probe = await probe_file(source)
    if not probe.has_audio:
        raise ProcessingError("В источнике нет аудио", code="NO_AUDIO")
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_name(output.name + ".part")
    cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-y", "-i", str(source),
           "-map", "0:a:0", "-vn", "-map_metadata", "0"]
    channels = min(2, probe.channels or 2)
    if target == "mp3":
        rate = probe.sample_rate if probe.sample_rate in (32000, 44100, 48000) else 48000
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{bitrate or 320}k",
                "-ar", str(rate), "-ac", str(channels),
                "-id3v2_version", "3", "-write_id3v1", "0"]
    elif target == "m4a":
        rate = probe.sample_rate or 44100
        if rate > 96000:
            rate = 96000
        cmd += ["-c:a", "aac", "-b:a", f"{bitrate or 256}k",
                "-ar", str(rate), "-ac", str(channels), "-movflags", "+faststart"]
    else:
        cmd += ["-c:a", "flac", "-compression_level", "8"]
    cmd += [str(part)]
    try:
        result = await run_process(cmd, timeout=timeout or music_settings.ffmpeg_timeout)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    if result.returncode != 0 or not part.exists() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise ProcessingError(
            f"FFmpeg не смог конвертировать: {result.stderr.strip()[-300:]}",
            code="CONVERT_FAILED")
    part.replace(output)
    out_probe = await probe_file(output)
    if not out_probe.has_audio:
        output.unlink(missing_ok=True)
        raise ProcessingError("Результат конвертации не читается", code="CONVERT_INVALID")
    if probe.duration and out_probe.duration:
        if abs(probe.duration - out_probe.duration) > 1.5:
            output.unlink(missing_ok=True)
            raise ProcessingError("Длительность после конвертации изменилась",
                                  code="CONVERT_DURATION")
    return out_probe


async def convert_for_telegram(src: str | Path, dest: str | Path) -> tuple[AudioProbe, str]:
    """FLAC/WAV → MP3 320 kbps (однократно), если пользователь явно попросил."""
    probe = await convert(src, dest, "mp3", bitrate=320)
    return probe, "mp3"


__all__ = ["audio_format", "is_telegram_audio", "media_kind_for", "mime_for",
           "telegram_filename", "convert", "convert_for_telegram"]
