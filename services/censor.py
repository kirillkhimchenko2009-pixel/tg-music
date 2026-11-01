"""Детектор «блюра» (замученных фонограмм): муты и бипы-«пики».

Работает после скачивания файла: ffmpeg декодирует его в PCM 16к моно,
numpy считает спектрограммы по окнам 0.2 с и ищет два типа признаков
замены нецензурных слов в СЕРЕДИНЕ трека (интро/аутро не считаем):

1) муты — вырезанные слова: короткие тишины (0.3–1.1 с) в окружении голоса;
2) бипы — «пиканье»: узкополосные постоянные тоны 0.3–0.9 с
   (концентрация энергии около одной частоты), ≥5 отрезков = точно заливка
   с замутами (на калибровке «Хаски — Панелька» даёт 18 отрезков,
   «Грибы — Тает лёд»/«Скриптонит — Мультибрендовый» — 0/1).

Порог подобран консервативно: ложноположительное решение (забраковать
нормальный трек) дороже пропущенного блюра, поэтому отбой только при
выраженной картине.
"""
import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

log = logging.getLogger("censor")

SR = 16000            # sample rate декода
WIN = 0.2             # окно анализа, с
MUTE_LOW = 0.0008     # «тишина» по rms
VOICED = 0.008        # «есть звук» по rms
END_SKIP = 0.12       # доли длины: не анализируем интро/аутро
BEEP_MIN, BEEP_MAX = 0.3, 0.9     # длительность бипа, с
BEEP_MIN_NO = 5       # сколько бипов-отрезков = вкладка «замученно»
MUTE_MIN_NO = 3       # сколько вырезок = вкладка «замученно»


def _ffmpeg() -> str:
    p = os.getenv("FFMPEG_BIN") or ""
    if p and os.path.exists(p):
        return p
    w = shutil.which("ffmpeg")
    if w:
        return w
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _decode(path: str) -> np.ndarray:
    """Файл → int16 моно PCM 16k через ffmpeg (stdout)."""
    ff = _ffmpeg()
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
           "-f", "s16le", "pipe:"]
    raw = subprocess.run(cmd, capture_output=True, timeout=120,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not raw.stdout:
        raise RuntimeError(f"ffmpeg stdout пуст: {raw.stderr[:200]!r}")
    x = np.frombuffer(raw.stdout, dtype=np.int16)
    if x.size < SR:  # меньше секунды — файл пустой/сломанный
        raise RuntimeError(f"файл {path}: ~{x.size / SR:.1f}с аудио")
    return x.astype(np.float32) / 32768.0


def _windows(x: np.ndarray, size: int) -> tuple[np.ndarray, int]:
    step = int(size * 0.5)
    n = max(0, (len(x) - size) // step)
    if n == 0:
        return np.empty((0, size), np.float32), step
    idx = np.arange(size)[None, :] + np.arange(n)[:, None] * step
    return x[idx], step


def analyze(x: np.ndarray) -> dict:
    W = int(WIN * SR)
    frames, step = _windows(x, W)
    out = {"mutes": 0, "beeps": 0, "mutes_list": [], "beeps_list": [],
           "beeps_ok": False, "mutes_ok": False}
    if len(frames) == 0:
        return out
    n_f = len(frames)
    t = np.arange(n_f) * (step / SR)
    t_start, t_end = END_SKIP * (len(x) / SR), (1 - END_SKIP) * (len(x) / SR)

    rms = np.sqrt((frames ** 2).mean(axis=1))
    voiced = rms > VOICED

    # --- бипы: узкополосные постоянные тоны ---
    sp = np.abs(np.fft.rfft(frames * np.hanning(W)[None, :], axis=1)) ** 2
    freqs = np.fft.rfftfreq(W, 1 / SR)
    lo, hi = 500, 2200
    band = (freqs >= lo) & (freqs <= hi)   # ЧИСТО частотная маска
    e_band = (sp * band).sum(axis=1)
    e_all = sp.sum(axis=1) + 1e-12
    bi = np.where(band)[0]             # бины рабочей полосы
    k = max(2, int(150 / (freqs[1] - freqs[0])))   # окно узости 150 Гц
    if len(bi) <= k + 1:
        return out
    cum = np.concatenate([np.zeros((n_f, 1), np.float64),
                          np.cumsum(sp[:, bi], axis=1)], axis=1)
    e_narrow = (cum[:, k:] - cum[:, :-k]).max(axis=1)
    tonal = ((e_narrow / (e_band + 1e-12)) > 0.50) \
        & ((e_band / e_all) > 0.55) & voiced \
        & (t >= t_start) & (t <= t_end)

    # сегменты БЕЗ склейки зазоров (тона музыкальных нот при склейке
    # сливаются в «удлинённые бипы» и дают ложные срабатывания)
    def segments(mask: np.ndarray, min_len: int, max_len: int | None) -> list:
        segs, start = [], None
        for i in range(n_f):
            if mask[i]:
                if start is None:
                    start = i
            elif start is not None:
                L = i - start
                if min_len <= L <= (max_len or n_f):
                    segs.append((start, i, i))
                start = None
        if start is not None:
            L = n_f - start
            if min_len <= L <= (max_len or n_f):
                segs.append((start, n_f, n_f))
        return segs

    beep_segs = segments(tonal, 3, None)   # ≥0.4 с непрерывного тона
    out["beeps"] = len(beep_segs)
    out["beeps_list"] = [(t[a], t[b]) for a, b, _ in beep_segs]

    # --- муты: короткие тишины в «звуковой» среде ---
    silent = (rms < MUTE_LOW) & (t >= t_start) & (t <= t_end - 1.1)
    # голос рядом: в ±2с до/после окна rms > voiced
    mid = np.median(rms[voiced]) if voiced.any() else 0.02
    ctx = np.convolve(np.r_[0, (rms > VOICED).astype(int), 0],
                      np.ones(5), mode="same")
    mute_segs = [s for s in segments(
        silent, max(2, int(0.3 / WIN)), int(1.1 / WIN))
        if ctx[max(0, s[0] - 3):min(n_f, s[2] + 4)].max() > 0]
    out["mutes"] = len(mute_segs)
    out["mutes_list"] = [(t[a], t[b]) for a, b, _ in mute_segs]
    out["beeps_ok"] = out["beeps"] >= BEEP_MIN_NO
    out["mutes_ok"] = out["mutes"] >= MUTE_MIN_NO
    return out


def _empty() -> dict:
    return {"beeps": 0, "mutes": 0, "beeps_ok": False, "mutes_ok": False,
            "censored": False, "beeps_list": [], "mutes_list": []}
    # noqa: E501


def check_sync(path: str) -> dict:
    """Синхронная проверка файла на признаки цензуры."""
    try:
        x = _decode(path)
    except Exception as e:
        log.warning("censor: не смогли декодировать %s: %s: %s",
                    path, type(e).__name__, str(e)[:160])
        return _empty()
    try:
        r = analyze(x)
    except Exception:
        log.exception("censor: анализ упал на %s", path)
        return _empty()
    r["censored"] = bool(r["beeps_ok"] or r["mutes_ok"])
    if r["censored"]:
        log.info(
            "censor: %s: похоже на «замученную» фонограмму "
            "(бипы=%s муты=%s)",
            os.path.basename(path), r["beeps"], r["mutes"])
    else:
        log.debug("censor: %s чист (%s бипов, %s мутов)",
                  os.path.basename(path), r["beeps"], r["mutes"])
    return r


async def check(path: str) -> dict:
    return await asyncio.to_thread(check_sync, path)
