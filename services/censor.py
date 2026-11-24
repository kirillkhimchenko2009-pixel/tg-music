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
MUFFLE_MIN_NO = 4     # сколько «замазок» (провал верхов в вокале)


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


def analyze(x: np.ndarray) -> dict:
    """Потоковый анализ батчами: НЕ проектируем все окна в память сразу
    (на Render free 512 МБ полная матрица окон 22k×3200 ≈ 800 МБ — ООМ)."""
    W = int(WIN * SR)
    half = W // 2
    n_f = max(0, (len(x) - W) // half + 1)
    out = {"mutes": 0, "beeps": 0, "mutes_list": [], "beeps_list": [],
           "beeps_ok": False, "mutes_ok": False}
    if n_f == 0:
        return out
    t = np.arange(n_f) * (half / SR)
    t_start = END_SKIP * (len(x) / SR)
    t_end = (1 - END_SKIP) * (len(x) / SR)

    freqs = np.fft.rfftfreq(W, 1 / SR)
    band = (freqs >= 500) & (freqs <= 2200)
    bi = np.where(band)[0]             # бины рабочей полосы
    k = max(2, int(150 / (freqs[1] - freqs[0])))   # окно узости 150 Гц
    if len(bi) <= k + 1:
        return out
    win = np.hanning(W).astype(np.float32)

    CH = 128   # кадров в батче — пик памяти всего ~20 МБ
    rms = np.empty(n_f, np.float32)
    e_band = np.empty(n_f, np.float64)
    e_all = np.empty(n_f, np.float64)
    e_narrow = np.empty(n_f, np.float64)
    e_hi = np.empty(n_f, np.float64)   # верха 2.5–6.5 кГц: для замазки
    hi_lo, hi_hi = 2500, 6500
    hi_band = (freqs >= hi_lo) & (freqs <= hi_hi)
    for i0 in range(0, n_f, CH):
        m = min(CH, n_f - i0)
        idx = (i0 + np.arange(m))[:, None] * half + np.arange(W)[None, :]
        fr = x[idx]
        rms[i0:i0 + m] = np.sqrt((fr ** 2).mean(axis=1))
        sp = np.abs(np.fft.rfft(fr * win, axis=1)) ** 2
        del fr
        s = sp[:, bi]
        cum = np.concatenate([np.zeros((m, 1), np.float64),
                              np.cumsum(s, axis=1)], axis=1)
        e_band[i0:i0 + m] = s.sum(axis=1)
        e_all[i0:i0 + m] = sp.sum(axis=1) + 1e-12
        e_narrow[i0:i0 + m] = (cum[:, k:] - cum[:, :-k]).max(axis=1)
        e_hi[i0:i0 + m] = sp[:, hi_band].sum(axis=1)
        del sp, s, cum, idx

    voiced = rms > VOICED
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
    # --- замазки: вокал есть, но верха (2.5–6.5 кГц) провалились против
    # локального фона — слово «приглушено» шумом/фильтром (ЯМ «чистые»
    # заливки делаются именно так: ни муты, ни бипы, звук смазан).
    # Строгие пороги: сегмент ≥0.3 с, отношение и к локальной медиане,
    # и к абсолютному уровню — иначе наловим тихих бриджей/лоу-фай записей.
    ratio_hi = e_hi / (e_all + 1e-12)
    voiced_idx = np.where(voiced)[0]
    if voiced_idx.size > 30:
        ref = np.full(n_f, float(np.median(ratio_hi[voiced_idx])) + 1e-9)
        for i in range(n_f):
            lo = max(0, i - 15)
            hi = min(n_f, i + 16)
            w = ratio_hi[lo:hi][voiced[lo:hi]]
            if w.size >= 5:
                ref[i] = np.median(w)
        muffled = voiced & (ratio_hi < 0.30 * ref) & (ratio_hi < 0.10) \
            & (t >= t_start) & (t <= t_end)
        muff_segs = segments(muffled, 3, 12)   # 0.3–1.2 с (хоп 0.1 с)
    else:
        muff_segs = []
    out["muffled"] = len(muff_segs)
    out["muffled_list"] = [(t[a], t[b]) for a, b, _ in muff_segs]
    out["beeps_ok"] = out["beeps"] >= BEEP_MIN_NO
    out["mutes_ok"] = out["mutes"] >= MUTE_MIN_NO
    out["muffled_ok"] = out["muffled"] >= MUFFLE_MIN_NO
    return out


def _empty() -> dict:
    return {"beeps": 0, "mutes": 0, "muffled": 0, "beeps_ok": False,
            "mutes_ok": False, "muffled_ok": False,
            "censored": False, "beeps_list": [], "mutes_list": [],
            "muffled_list": []}
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
    # замазки: пока калибровка не завершена — в вердикт не включаем
    # (много ложных срабатываний на тёмных трэп-миксах)
    r["censored"] = bool(r["beeps_ok"] or r["mutes_ok"])
    if r["censored"]:
        log.info(
            "censor: %s: похоже на «замученную» фонограмму "
            "(бипы=%s муты=%s замазки=%s)",
            os.path.basename(path), r["beeps"], r["mutes"], r["muffled"])
    else:
        log.debug("censor: %s чист (%s бипов, %s мутов)",
                  os.path.basename(path), r["beeps"], r["mutes"])
    return r


async def check(path: str) -> dict:
    return await asyncio.to_thread(check_sync, path)
