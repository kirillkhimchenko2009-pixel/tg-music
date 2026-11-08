"""FFprobe-анализ контейнера + FFmpeg-анализ артефактов и качества."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from ..config import music_settings
from ..domain import AudioAnalysis, AudioProbe
from ..enums import QualityCategory
from ..errors import ProcessingError
from ..ffmpeg import ffmpeg_path, ffprobe_json, run_process
from .bitrate_checker import assess, estimate_bandwidth
from .clipping_detector import ClipStats, scan_pcm
from .cover_checker import has_embedded_cover
from .metadata_checker import MetadataReport, inspect
from .validator import fill_analysis

log = logging.getLogger("music.quality.analyzer")

_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(
    r"silence_end:\s*(-?\d+(?:\.\d+)?)\s*\|\s*silence_duration:\s*(-?\d+(?:\.\d+)?)")
_TRUE_PEAK = re.compile(r"True peak\s*:?\s*(-?\d+(?:\.\d+)?)\s*dB", re.I)
_CORRUPT_HINTS = ("corrupt", "invalid data", "truncat", "error while decoding",
                  "could not find codec", "moov atom not found")


def _container(format_name: str, codec: str) -> str:
    name = (format_name or "").lower()
    codec = (codec or "").lower()
    if "mp4" in name or "mov," in name or name == "mov":
        return "m4a" if codec in ("aac", "alac") else "mp4"
    if "matroska" in name or "webm" in name:
        return "webm"
    if "ogg" in name:
        return "opus" if codec == "opus" else "ogg"
    if "mpeg" in name and codec in ("mp3", "mp2"):
        return "mp3"
    for token in name.split(","):
        token = token.strip()
        if token in ("mp3", "flac", "wav", "aac", "m4a", "ogg", "opus", "webm", "aiff"):
            return token
    return name.split(",")[0] if name else (codec or "unknown")


def _as_int(value) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_probe(data: dict, size: int = 0, stderr: str = "") -> AudioProbe:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    codec = str((audio or {}).get("codec_name") or "")
    container = _container(str(fmt.get("format_name") or ""), codec)
    tags: dict[str, str] = {}
    for source in ((audio or {}).get("tags") or {}, fmt.get("tags") or {}):
        for key, value in source.items():
            if value is not None:
                tags[str(key)] = str(value)
    duration = 0.0
    for value in (fmt.get("duration"), (audio or {}).get("duration")):
        try:
            duration = float(value)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue
    bitrate = _as_int((audio or {}).get("bit_rate")) or _as_int(fmt.get("bit_rate"))
    probe = AudioProbe(
        codec=codec,
        container=container,
        format_name=str(fmt.get("format_name") or ""),
        bitrate_kbps=round(bitrate / 1000) if bitrate else None,
        sample_rate=_as_int((audio or {}).get("sample_rate")),
        bit_depth=(_as_int((audio or {}).get("bits_per_raw_sample"))
                   or _as_int((audio or {}).get("bits_per_sample"))),
        channels=_as_int((audio or {}).get("channels")),
        duration=round(duration, 3),
        size=int(size or _as_int(fmt.get("size")) or 0),
        tags=tags,
        has_audio=audio is not None,
        has_video=bool(video),
    )
    if probe.duration <= 0:
        probe.warnings.append("не удалось определить длительность")
    if len([s for s in streams if s.get("codec_type") == "audio"]) > 1:
        probe.warnings.append("несколько аудиопотоков — анализируется первый")
    low = (stderr or "").lower()
    for hint in _CORRUPT_HINTS:
        if hint in low:
            probe.warnings.append(f"FFprobe: {hint}")
            break
    if probe.size == 0:
        probe.warnings.append("нулевой размер файла")
    return probe


async def probe_file(path: str | Path, *, timeout: float | None = None) -> AudioProbe:
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise ProcessingError(f"Файл недоступен: {exc}", code="FILE_MISSING") from exc
    if size == 0:
        probe = AudioProbe(size=0, ffprobe_error="пустой файл")
        probe.warnings.append("пустой файл")
        return probe
    result = await ffprobe_json(str(p), timeout=timeout)
    if result.returncode != 0:
        probe = AudioProbe(size=size, ffprobe_error=result.stderr.strip()[-400:] or
                           f"ffprobe exit {result.returncode}")
        return probe
    try:
        data = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        probe = AudioProbe(size=size, ffprobe_error=f"bad json: {exc}")
        return probe
    return parse_probe(data, size=size, stderr=result.stderr)


async def _true_peak(path: str, timeout: float | None = None) -> float | None:
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", str(path),
           "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"]
    try:
        result = await run_process(cmd, timeout=timeout, max_stdout=1024 * 1024)
    except ProcessingError:
        return None
    matches = _TRUE_PEAK.findall(result.stderr)
    if not matches:
        return None
    try:
        return round(float(matches[-1]), 2)
    except ValueError:
        return None


async def _silence(path: str, duration: float,
                   timeout: float | None = None) -> tuple[float, float, float]:
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", str(path),
           "-map", "0:a:0", "-af", "silencedetect=noise=-50dB:d=2",
           "-f", "null", "-"]
    try:
        result = await run_process(cmd, timeout=timeout, max_stdout=1024 * 1024)
    except ProcessingError:
        return 0.0, 0.0, 0.0
    text = result.stderr
    starts = [float(x) for x in _SILENCE_START.findall(text)]
    ends = [(float(a), float(b)) for a, b in _SILENCE_END.findall(text)]
    if not starts and not ends:
        return 0.0, 0.0, 0.0
    total = sum(max(0.0, d) for _, d in ends)
    leading = 0.0
    trailing = 0.0
    if starts:
        first = max(0.0, starts[0])
        if first <= 0.1:
            leading = (ends[0][0] if ends else min(duration, first + 2)) - first
    if ends and duration > 0:
        last_end, last_dur = ends[-1]
        if last_end >= duration - 1.0:
            trailing = max(0.0, last_dur)
        elif len(starts) > len(ends):
            trailing = max(0.0, duration - starts[-1])
    return round(leading, 2), round(trailing, 2), round(total, 2)


async def analyze_audio(path: str | Path, *, expected_duration: int = 0,
                        expected_title: str = "", expected_artist: str = "",
                        expected_album: str = "", deep: bool = True,
                        timeout: float | None = None) -> AudioAnalysis:
    p = Path(path)
    probe = await probe_file(p, timeout=timeout)
    analysis = AudioAnalysis(probe=probe)
    if probe.ffprobe_error or not probe.has_audio:
        clip = ClipStats()
        analysis.category = fill_analysis(
            analysis, clip=clip, metadata=None,
            min_duration=music_settings.min_duration_sec).category
        analysis.notes.append("Структурная проверка не пройдена")
        return analysis

    clip = ClipStats()
    bandwidth_hz = None
    likely_transcoded = False
    transcode_reason = ""
    leading = trailing = silence_total = 0.0
    if deep:
        clip, true_peak, silence, bandwidth_hz = await asyncio.gather(
            scan_pcm(str(p), timeout=timeout),
            _true_peak(str(p), timeout=timeout),
            _silence(str(p), probe.duration, timeout=timeout),
            estimate_bandwidth(str(p), probe.duration, timeout=timeout),
        )
        leading, trailing, silence_total = silence
        analysis.true_peak_dbfs = true_peak
        info = assess(probe.codec, probe.bitrate_kbps, probe.sample_rate,
                      bandwidth_hz, probe.duration, probe.is_lossless)
        bandwidth_hz = info.bandwidth_hz
        likely_transcoded = info.likely_transcoded
        transcode_reason = info.reason
    try:
        has_cover = has_embedded_cover(p)
    except Exception:  # noqa: BLE001
        has_cover = False
    metadata = inspect(
        probe.tags, expected_title=expected_title, expected_artist=expected_artist,
        expected_album=expected_album, expected_duration=expected_duration,
        actual_duration=probe.duration, filename=p.name, has_cover=has_cover)
    fill_analysis(analysis, clip=clip, metadata=metadata,
                  silence_leading=leading, silence_trailing=trailing,
                  silence_total=silence_total, bandwidth_hz=bandwidth_hz,
                  likely_transcoded=likely_transcoded,
                  transcode_reason=transcode_reason,
                  expected_duration=expected_duration,
                  min_duration=music_settings.min_duration_sec)
    if probe.duration > music_settings.max_duration_sec:
        analysis.add_issue("too_long", "Файл длиннее допустимого лимита", "error")
        analysis.category = QualityCategory.DAMAGED
        analysis.score = 0
    return analysis


async def analyze_metadata_only(path: str | Path) -> MetadataReport:
    probe = await probe_file(path)
    has_cover = has_embedded_cover(path)
    return inspect(probe.tags, actual_duration=probe.duration,
                   filename=Path(path).name, has_cover=has_cover)


__all__ = ["probe_file", "analyze_audio", "analyze_metadata_only", "parse_probe"]
