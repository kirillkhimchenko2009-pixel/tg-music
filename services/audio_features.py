"""Аудио-анализ песни: BPM, энергия, «валентность» (веселье↔грусть),
танцевальность — реальные признаки звука, а не слова в названии.

Зачем: настройки волны «весёлое / энергичное / спокойное / грустное / жёсткое»
раньше работали только если слово настроения встречалось в названии трека
(редко). Здесь ffmpeg декодирует 45-секундный фрагмент (с 15-й секунды —
интро пропускаем), numpy считает:

  • BPM            — автокорреляция огибающей онсетов (60–180);
  • energy (0..1)  — средняя громкость RMS в dB, нормированная;
  • valence (0..1) — мажор/минор по хроме (профили Крумхансла) + яркость;
  • danceability   — сила/регулярность бита (пик автокорреляции) + энергия.

Никаких внешних сервисов и ML-моделей: только ffmpeg и numpy (уже в стеке —
детектор цензуры использует тот же подход). Результат пишется в tracks
(bpm/energy/valence/danceability/analyzed_at) и используется ранжировщиком
волны как фактор mood_audio.

Анализ не критичен для доставки файла: любая ошибка глушится вызывающим.
"""
from __future__ import annotations

import asyncio
import logging
import math
import shutil
import subprocess

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Track, utcnow

log = logging.getLogger("audio_features")

SR = 22050            # частота декода, Гц
FRAME = 2048          # окно FFT (~93 мс)
HOP = 1024            # шаг (~46 мс)
SLICE_START = 15.0    # пропускаем интро
SLICE_LEN = 45.0      # анализируем 45 секунд
SHORT_START = 0.0     # короткие треки — с начала
SHORT_LEN = 30.0
MIN_SAMPLES = SR      # меньше секунды звука — не анализируем
RMS_SILENCE = 0.001   # порог «тишины» кадра

# профили Крумхансла (мажор/минор) для оценки валентности по хроме
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                   2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                   2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _ffmpeg() -> str | None:
    try:
        from music.config import music_settings
        exe = music_settings.ffmpeg_bin or "ffmpeg"
    except Exception:            # noqa: BLE001 — конфиг воркера может не грузиться
        exe = "ffmpeg"
    return shutil.which(exe) or shutil.which("ffmpeg")


def _decode(path: str, start: float, length: float) -> np.ndarray | None:
    exe = _ffmpeg()
    if not exe:
        return None
    cmd = [exe, "-v", "error", "-nostdin",
           "-ss", f"{start:.1f}", "-t", f"{length:.1f}", "-i", str(path),
           "-vn", "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    except Exception as e:       # noqa: BLE001
        log.warning("audio_features: ffmpeg не отработал: %s", type(e).__name__)
        return None
    if r.returncode != 0 or len(r.stdout) < MIN_SAMPLES * 2:
        return None
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def decode_pcm(path: str) -> np.ndarray | None:
    """45 с с 15-й секунды; короткий трек — 30 с с начала."""
    pcm = _decode(path, SLICE_START, SLICE_LEN)
    if pcm is not None:
        return pcm
    return _decode(path, SHORT_START, SHORT_LEN)


def _frames(x: np.ndarray) -> np.ndarray:
    if len(x) < FRAME:
        x = np.pad(x, (0, FRAME - len(x)))
    view = np.lib.stride_tricks.sliding_window_view(x, FRAME)
    return view[::HOP]


def _clamp01(v: float) -> float:
    return float(min(1.0, max(0.0, v)))


def _chroma(frames: np.ndarray) -> np.ndarray:
    """12 pitch-class профилей по спектру (80–2000 Гц), нормированы в сумму 1.

    Считаем только локальные пики спектра: у синусоиды/инструмента боковые
    бины окна — это утечка, а не отдельные ноты. Без отбора пиков утечка
    размазывала хрому по соседним полутонам и мажор/минор путались.
    """
    freqs = np.fft.rfftfreq(FRAME, 1.0 / SR)
    band = (freqs >= 80.0) & (freqs <= 2000.0)
    idx = np.flatnonzero(band)
    fb = freqs[idx]
    # частота → MIDI → pitch class
    midi = 69.0 + 12.0 * np.log2(np.maximum(fb, 1e-9) / 440.0)
    pcs = np.mod(np.round(midi).astype(int), 12)
    window = np.hanning(FRAME).astype(np.float32)
    acc = np.zeros(12, dtype=np.float64)
    for fr in frames[::2]:               # каждый второй кадр — достаточно
        mag = np.abs(np.fft.rfft(fr * window))[idx]
        if mag.size < 3:
            continue
        peak = np.zeros(mag.size, dtype=bool)
        peak[1:-1] = (mag[1:-1] > mag[:-2]) & (mag[1:-1] >= mag[2:])
        if np.any(peak):
            np.add.at(acc, pcs[peak], mag[peak])
    s = acc.sum()
    return acc / s if s > 0 else np.zeros(12)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / den) if den > 0 else 0.0


def _valence(chroma: np.ndarray) -> float:
    """Мажор/минор: максимальная корреляция по всем тоникам, дельта → 0..1."""
    best_maj = best_min = -2.0
    for r in range(12):
        cm = _corr(chroma, np.roll(_MAJOR, r))
        cn = _corr(chroma, np.roll(_MINOR, r))
        best_maj = max(best_maj, cm)
        best_min = max(best_min, cn)
    delta = best_maj - best_min           # [-2..2], обычно [-0.5..0.5]
    return _clamp01(0.5 + 0.9 * delta)


def _bpm(envelope: np.ndarray) -> tuple[float, float]:
    """Автокорреляция огибающей онсетов → (BPM, уверенность 0..1)."""
    if envelope.size < 32:
        return 0.0, 0.0
    env = envelope - envelope.mean()
    ac = np.correlate(env, env, mode="full")[env.size - 1:]
    if ac[0] <= 0:
        return 0.0, 0.0
    fps = SR / HOP
    # лаги для 50..200 BPM
    lo = max(2, int(round(60.0 * fps / 200.0)))
    hi = min(ac.size - 2, int(round(60.0 * fps / 50.0)))
    if hi <= lo:
        return 0.0, 0.0
    seg = ac[lo:hi + 1]
    lag = lo + int(np.argmax(seg))
    # параболическая интерполяция пика — точнее 1 кадр
    if 0 < lag < ac.size - 1 and ac[lag] > 0:
        y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
        lag_f = lag + float(np.clip(frac, -0.5, 0.5))
    else:
        lag_f = float(lag)
    bpm = 60.0 * fps / lag_f
    # гармоническая неоднозначность: сводим в 70..160
    while bpm < 70.0:
        bpm *= 2.0
    while bpm >= 160.0:
        bpm /= 2.0
    conf = _clamp01(float(ac[lag] / ac[0]) * 2.5)
    return float(bpm), conf


def analyze(path: str) -> dict | None:
    """Признаки звука файла. None — файл не декодировался."""
    pcm = decode_pcm(path)
    if pcm is None or pcm.size < MIN_SAMPLES:
        return None
    frames = _frames(pcm)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    voiced = rms > RMS_SILENCE
    if not np.any(voiced):
        return None
    rms_v = rms[voiced]
    db = 20.0 * np.log10(np.maximum(rms_v, 1e-9))
    # типичная музыка: −35..−8 dB → 0..1
    energy = _clamp01((float(np.mean(db)) + 35.0) / 27.0)

    # онсеты: положительные приросты лог-энергии
    log_e = np.log(np.maximum(rms, 1e-6))
    env = np.maximum(0.0, np.diff(log_e))
    bpm, beat_conf = _bpm(env)

    chroma = _chroma(frames[voiced]) if np.any(voiced) else np.zeros(12)
    valence = _valence(chroma) if chroma.sum() > 0 else 0.5
    # минор звучит печальнее; яркость спектра слегка поднимает «веселье»
    windows = np.hanning(FRAME).astype(np.float32)
    cents = []
    freqs = np.fft.rfftfreq(FRAME, 1.0 / SR)
    for fr in frames[voiced][::4]:
        mag = np.abs(np.fft.rfft(fr * windows))
        s = mag.sum()
        if s > 0:
            cents.append(float(np.dot(freqs, mag) / s))
    if cents:
        bright = _clamp01((float(np.median(cents)) - 600.0) / 2600.0)
        valence = _clamp01(0.85 * valence + 0.15 * bright)

    dance = _clamp01(0.6 * beat_conf + 0.4 * energy)
    return {"bpm": round(bpm, 1), "energy": round(energy, 3),
            "valence": round(valence, 3),
            "danceability": round(dance, 3)}


def apply_to_track(track: Track, feats: dict) -> None:
    track.bpm = float(feats.get("bpm") or 0.0)
    track.energy = float(feats.get("energy") or 0.0)
    track.valence = float(feats.get("valence") or 0.0)
    track.danceability = float(feats.get("danceability") or 0.0)
    track.analyzed_at = utcnow()


async def analyze_and_save(session: AsyncSession, track: Track,
                           path: str) -> dict | None:
    """Анализ в отдельном потоке (ffmpeg/numpy не блокируют event loop)."""
    try:
        feats = await asyncio.to_thread(analyze, str(path))
    except Exception as e:       # noqa: BLE001
        log.warning("audio_features: анализ трека %s упал: %s: %s",
                    getattr(track, "id", "?"), type(e).__name__, e)
        return None
    if not feats:
        return None
    apply_to_track(track, feats)
    try:
        await session.flush()
        await session.commit()
    except Exception:            # noqa: BLE001
        log.warning("audio_features: признаки трека %s не сохранились",
                    getattr(track, "id", "?"))
    log.info("audio_features: %s — bpm=%.0f energy=%.2f valence=%.2f dance=%.2f",
             getattr(track, "id", "?"), feats["bpm"], feats["energy"],
             feats["valence"], feats["danceability"])
    return feats
