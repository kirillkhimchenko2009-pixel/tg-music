"""Оценка битрейта и спектральной полосы: признаки перекодирования."""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

import numpy as np

from ..config import music_settings
from ..ffmpeg import ffmpeg_path

log = logging.getLogger("music.quality.bitrate")

_LOSSY = {"mp3", "aac", "m4a", "ogg", "vorbis", "opus", "webm", "wma", "mp2"}
_LOSSLESS = {"flac", "alac", "wav", "aiff", "ape", "wv", "pcm_s16le",
             "pcm_s24le", "pcm_s32le", "pcm_f32le"}


def is_lossy(codec: str) -> bool:
    return (codec or "").lower() in _LOSSY


def is_lossless(codec: str) -> bool:
    return (codec or "").lower() in _LOSSLESS


def bitrate_tier(codec: str, bitrate: int | None) -> str:
    """high | standard | low | unknown для lossy-кодеков."""
    codec = (codec or "").lower()
    if not bitrate:
        return "unknown"
    if codec == "opus":
        return "high" if bitrate >= 160 else ("standard" if bitrate >= 96 else "low")
    if codec in ("ogg", "vorbis"):
        return "high" if bitrate >= 224 else ("standard" if bitrate >= 128 else "low")
    return "high" if bitrate >= 256 else ("standard" if bitrate >= 160 else "low")


def codec_family(container: str, codec: str) -> str:
    c = (codec or "").lower()
    fmt = (container or "").lower()
    if c in ("mp3", "mp2"):
        return "mp3"
    if c in ("aac", "alac"):
        return "aac"
    if c in ("flac",):
        return "flac"
    if c.startswith("pcm"):
        return "wav"
    if c in ("vorbis", "opus"):
        return c
    if fmt in ("mp3", "flac", "wav", "ogg"):
        return fmt
    return c or fmt or "unknown"


@dataclass(slots=True)
class BandwidthInfo:
    bandwidth_hz: int | None = None
    likely_transcoded: bool = False
    reason: str = ""


async def estimate_bandwidth(path: str, duration: float = 0.0,
                              timeout: float | None = None) -> int | None:
    """Средняя спектральная граница по 40-секундному фрагменту."""
    offset = 0.0
    if duration > 80:
        offset = min(duration * 0.25, 120.0)
    cmd = [ffmpeg_path(), "-v", "error", "-nostdin"]
    if offset:
        cmd += ["-ss", f"{offset:.2f}"]
    cmd += ["-t", "40", "-i", str(path), "-map", "0:a:0",
            "-ac", "1", "-ar", "44100", "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        raw, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout or music_settings.ffmpeg_timeout)
    except Exception as exc:  # noqa: BLE001
        log.debug("bandwidth: %s", exc)
        return None
    if not raw:
        return None
    samples = np.frombuffer(raw[:len(raw) - len(raw) % 4], dtype="<f4")
    if samples.size < 16384:
        return None
    window, hop = 8192, 4096
    windows = []
    hann = np.hanning(window).astype(np.float32)
    for start in range(0, len(samples) - window, hop):
        frame = samples[start:start + window]
        if float(np.sqrt(np.mean(frame * frame))) < 0.001:
            continue
        windows.append(np.abs(np.fft.rfft(frame * hann)) ** 2)
        if len(windows) >= 240:
            break
    if not windows:
        return None
    spectrum = np.mean(np.vstack(windows), axis=0)
    if not np.isfinite(spectrum).all():
        return None
    freqs = np.fft.rfftfreq(window, 1 / 44100)
    peak = float(spectrum.max())
    if peak <= 0:
        return None
    db = 10 * np.log10(np.maximum(spectrum / peak, 1e-12))
    # сглаживание по 5 бинам
    if len(db) >= 5:
        kernel = np.ones(5) / 5
        db = np.convolve(db, kernel, mode="same")
    threshold = max(-80.0, float(db.max()) - 72.0)
    above = db > threshold
    # требуем 3 подряд идущих бина выше порога
    idx = None
    run = 0
    for i, value in enumerate(above):
        if value:
            run += 1
            if run >= 3:
                idx = i
        else:
            run = 0
    if idx is None:
        return None
    return int(round(float(freqs[idx]) / 100.0) * 100)


def assess(codec: str, bitrate: int | None, sample_rate: int | None,
           bandwidth_hz: int | None, duration: float = 0.0,
           lossless: bool = False) -> BandwidthInfo:
    if not bandwidth_hz or duration < 10:
        return BandwidthInfo(bandwidth_hz=bandwidth_hz)
    cutoff_khz = bandwidth_hz / 1000.0
    if lossless:
        if cutoff_khz < 18.0 and duration >= 30:
            return BandwidthInfo(bandwidth_hz,
                                 True,
                                 f"lossless-контейнер с полосой {cutoff_khz:.1f} кГц — "
                                 "возможен апскейл из lossy")
        return BandwidthInfo(bandwidth_hz)
    if not is_lossy(codec):
        return BandwidthInfo(bandwidth_hz)
    if sample_rate and sample_rate < 30000:
        return BandwidthInfo(bandwidth_hz, True,
                             f"низкая частота дискретизации {sample_rate} Гц")
    if bitrate and bitrate >= 240 and cutoff_khz <= 17.0:
        return BandwidthInfo(bandwidth_hz, True,
                             f"битрейт {bitrate} kbps, но полоса всего {cutoff_khz:.1f} кГц")
    if bitrate and bitrate >= 180 and cutoff_khz <= 15.5:
        return BandwidthInfo(bandwidth_hz, True,
                             f"битрейт {bitrate} kbps не соответствует полосе {cutoff_khz:.1f} кГц")
    return BandwidthInfo(bandwidth_hz)


__all__ = ["is_lossy", "is_lossless", "bitrate_tier", "codec_family",
           "BandwidthInfo", "estimate_bandwidth", "assess"]
