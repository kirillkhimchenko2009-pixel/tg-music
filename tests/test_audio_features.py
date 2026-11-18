# -*- coding: utf-8 -*-
"""Аудио-анализ под настроения волны (services/audio_features.py).

Проверяются реальные вычисления на синтетическом звуке:
  • BPM клика 120 → ~120 (допуск ±10);
  • громкий трек энергичнее тихого;
  • мажор «веселее» минора по валентности;
  • фактор mood_audio: энергичное/спокойное/грустное выбирают разные треки;
  • analyze_and_save пишет признаки в Track и не падает без файла.

Генерация WAV — numpy + stdlib wave, декодирование — ffmpeg (как в проде).
Без ffmpeg аудио-тесты пропускаются, а не падают.
"""
from __future__ import annotations

import math
import shutil
import wave

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

from core.models import Base, Track
from services import audio_features
from services.wave.intel.context import Scenario
from services.wave.intel.ranker import _mood_audio_fit

SR = audio_features.SR


def _need_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg не установлен — аудио-тесты пропущены")


def _write_wav(path, samples: np.ndarray) -> str:
    pcm = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((pcm * 32767.0).astype("<i2").tobytes())
    return str(path)


def _clicks(bpm: float, seconds: float, amp: float = 0.8) -> np.ndarray:
    """Периодические удары (низкий тон с затуханием) — чёткий бит для BPM."""
    n = int(SR * seconds)
    x = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    length = int(0.12 * SR)
    t = np.arange(length) / SR
    hit = (amp * np.sin(2.0 * math.pi * 80.0 * t)
           * np.exp(-t * 35.0)).astype(np.float32)
    pos = 0.0
    while True:
        i = int(pos * SR)
        if i >= n:
            break
        seg = min(length, n - i)
        x[i:i + seg] += hit[:seg]
        pos += period
    return x


def _sine(freq: float, seconds: float, amp: float) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2.0 * math.pi * freq * t)).astype(np.float32)


def _chord(freqs: list[float], seconds: float = 18.0,
           amp: float = 0.08) -> np.ndarray:
    """Непрерывный аккорд (тоника + терция + квинта): чистая хрома без кликов."""
    total = int(SR * seconds)
    t = np.arange(total) / SR
    x = np.zeros(total, dtype=np.float32)
    for f in freqs:
        x += (amp * np.sin(2.0 * math.pi * f * t)).astype(np.float32)
    fade = int(0.05 * SR)
    x[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
    x[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return x


# До-мажор и до-минор: один тоник, разная терция — «весело» vs «грустно»
_MAJOR_CHORD = [130.81, 261.63, 329.63, 392.00]     # C3 C4 E4 G4
_MINOR_CHORD = [130.81, 261.63, 311.13, 392.00]     # C3 C4 Eb4 G4


@pytest.fixture(scope="module")
def audio(tmp_path_factory):
    _need_ffmpeg()
    folder = tmp_path_factory.mktemp("features")
    return {
        "click120": _write_wav(folder / "click120.wav", _clicks(120.0, 24.0)),
        "loud": _write_wav(
            folder / "loud.wav",
            _sine(220.0, 24.0, 0.5) + _sine(440.0, 24.0, 0.3)),
        "quiet": _write_wav(folder / "quiet.wav", _sine(220.0, 24.0, 0.04)),
        "major": _write_wav(folder / "major.wav", _chord(_MAJOR_CHORD)),
        "minor": _write_wav(folder / "minor.wav", _chord(_MINOR_CHORD)),
    }


def test_bpm_click_120(audio):
    feats = audio_features.analyze(audio["click120"])
    assert feats is not None
    assert abs(feats["bpm"] - 120.0) <= 10.0


def test_energy_loud_more_than_quiet(audio):
    loud = audio_features.analyze(audio["loud"])
    quiet = audio_features.analyze(audio["quiet"])
    assert loud is not None and quiet is not None
    assert loud["energy"] > quiet["energy"] + 0.2


def test_valence_major_more_than_minor(audio):
    major = audio_features.analyze(audio["major"])
    minor = audio_features.analyze(audio["minor"])
    assert major is not None and minor is not None
    assert major["valence"] > minor["valence"]


def _track(bpm=0.0, energy=0.0, valence=0.0, dance=0.0,
           analyzed=True) -> Track:
    from core.models import utcnow
    t = Track(source="test", external_id="x", title="t", artist="a")
    t.bpm, t.energy, t.valence, t.danceability = bpm, energy, valence, dance
    t.analyzed_at = utcnow() if analyzed else None
    return t


def _scen(*moods: str) -> Scenario:
    return Scenario(id="test", title="t", description="d", moods=frozenset(moods))


def test_mood_audio_neutral_without_analysis_or_mood():
    no_feats = _track(analyzed=False)
    fast = _track(bpm=150.0, energy=0.9, valence=0.8, dance=0.8)
    assert _mood_audio_fit(no_feats, _scen("active")) == 0.5
    assert _mood_audio_fit(fast, _scen()) == 0.5
    assert _mood_audio_fit(fast, None) == 0.5


def test_mood_audio_active_vs_calm():
    fast = _track(bpm=150.0, energy=0.9, valence=0.7, dance=0.8)
    slow = _track(bpm=70.0, energy=0.2, valence=0.5, dance=0.2)
    assert _mood_audio_fit(fast, _scen("active")) > _mood_audio_fit(slow, _scen("active"))
    assert _mood_audio_fit(slow, _scen("calm")) > _mood_audio_fit(fast, _scen("calm"))


def test_mood_audio_sad_prefers_minor_and_slow():
    happy = _track(bpm=140.0, energy=0.85, valence=0.9, dance=0.8)
    sad = _track(bpm=65.0, energy=0.2, valence=0.1, dance=0.2)
    assert _mood_audio_fit(sad, _scen("sad")) > _mood_audio_fit(happy, _scen("sad"))
    assert _mood_audio_fit(sad, _scen("sad")) > 0.6


def test_mood_audio_fun_and_edge():
    fun = _track(bpm=125.0, energy=0.8, valence=0.9, dance=0.9)
    dull = _track(bpm=100.0, energy=0.3, valence=0.4, dance=0.2)
    assert _mood_audio_fit(fun, _scen("fun")) > _mood_audio_fit(dull, _scen("fun"))
    hard = _track(bpm=160.0, energy=0.95, valence=0.15, dance=0.7)
    soft = _track(bpm=80.0, energy=0.25, valence=0.8, dance=0.3)
    assert _mood_audio_fit(hard, _scen("edge")) > _mood_audio_fit(soft, _scen("edge"))


@pytest.mark.asyncio
async def test_analyze_and_save_writes_track(audio):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as s:
            t = Track(source="test", external_id="f1", title="click", artist="x")
            s.add(t)
            await s.commit()
            feats = await audio_features.analyze_and_save(s, t, audio["click120"])
            assert feats is not None
            assert t.analyzed_at is not None
            assert t.energy > 0.0
            assert abs(t.bpm - 120.0) <= 10.0
            await s.refresh(t)
            assert t.analyzed_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_analyze_and_save_bad_file_is_quiet(tmp_path):
    _need_ffmpeg()
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as s:
            t = Track(source="test", external_id="bad", title="bad", artist="x")
            s.add(t)
            await s.commit()
            bad = tmp_path / "bad.mp3"
            bad.write_bytes(b"not audio at all")
            assert await audio_features.analyze_and_save(s, t, str(bad)) is None
            assert t.analyzed_at is None       # ничего не записали
    finally:
        await engine.dispose()
