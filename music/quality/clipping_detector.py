"""Детектор цифрового клиппинга и перегрузки по PCM-потоку FFmpeg."""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

import numpy as np

from ..config import music_settings
from ..errors import ProcessingError
from ..ffmpeg import ffmpeg_path

log = logging.getLogger("music.quality.clipping")


@dataclass(slots=True)
class ClipStats:
    peak_dbfs: float | None = None
    rms_dbfs: float | None = None
    clipped_samples: int = 0
    clipped_ratio: float = 0.0
    clipping: bool = False
    long_runs: int = 0
    decoded_samples: int = 0
    decode_failed: bool = False
    error: str = ""


def _db(value: float) -> float:
    return round(20.0 * math.log10(max(value, 1e-12)), 2)


async def scan_pcm(path: str, *, timeout: float | None = None) -> ClipStats:
    """Декодирует в float32 stereo 44.1k и считает пики/плоские вершины."""
    cmd = [ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path),
           "-map", "0:a:0", "-ac", "2", "-ar", "44100",
           "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"]
    stats = ClipStats()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        stats.decode_failed = True
        stats.error = "ffmpeg не найден"
        return stats
    peak = 0.0
    sum_sq = 0.0
    total = 0
    clipped = 0
    long_runs = 0
    run = 0
    leftover = b""
    full = np.float32(0.99999)
    try:
        while True:
            chunk = await asyncio.wait_for(
                proc.stdout.read(512 * 1024), timeout=timeout or music_settings.ffmpeg_timeout)
            if not chunk:
                break
            data = leftover + chunk
            usable = len(data) - (len(data) % 4)
            leftover = data[usable:]
            if usable:
                samples = np.frombuffer(data[:usable], dtype="<f4")
                if samples.size:
                    absolute = np.abs(samples)
                    peak = max(peak, float(absolute.max()))
                    sum_sq += float(np.square(samples, dtype=np.float64).sum())
                    total += int(samples.size)
                    hit = absolute >= full
                    hits = int(hit.sum())
                    clipped += hits
                    if hits:
                        # длины непрерывных плоских вершин
                        padded = np.concatenate(([False], hit, [False]))
                        edges = np.flatnonzero(padded[1:] != padded[:-1])
                        lengths = edges[1::2] - edges[::2]
                        long_runs += int((lengths >= 4).sum())
        await asyncio.wait_for(proc.wait(), timeout=15)
        if proc.returncode not in (0, None) and total == 0:
            stats.decode_failed = True
            err = await proc.stderr.read()
            stats.error = err.decode("utf-8", errors="replace")[-300:]
    except (asyncio.TimeoutError, OSError) as exc:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        stats.decode_failed = True
        stats.error = f"{type(exc).__name__}: {exc}"
        return stats

    stats.decoded_samples = total
    if total:
        stats.peak_dbfs = _db(peak)
        stats.rms_dbfs = _db(math.sqrt(sum_sq / total))
        stats.clipped_samples = clipped
        stats.clipped_ratio = round(clipped / total, 8)
        stats.long_runs = long_runs
        stats.clipping = long_runs > 0 or stats.clipped_ratio > 0.001
    return stats


def describe(stats: ClipStats) -> list[str]:
    issues = []
    if stats.decode_failed:
        issues.append("аудиопоток не декодируется")
    if stats.clipping:
        issues.append(f"цифровой клиппинг: {stats.clipped_samples} сэмплов, "
                      f"{stats.long_runs} плоских вершин")
    if stats.peak_dbfs is not None and stats.peak_dbfs > -0.1 and not stats.clipping:
        issues.append("сигнал на пределе 0 dBFS (возможна перегрузка)")
    if stats.rms_dbfs is not None and stats.rms_dbfs > -6:
        issues.append("очень высокая средняя громкость (перегрузка)")
    return issues


__all__ = ["ClipStats", "scan_pcm", "describe"]
