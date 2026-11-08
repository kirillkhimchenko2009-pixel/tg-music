"""Общие фикстуры: синтетическое аудио, изолированная БД, временные каталоги."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from music.config import music_settings  # noqa: E402
from music.models import MusicBase  # noqa: E402


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("FFmpeg не установлен — тесты аудио пропущены")
    return exe


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, check=False)


@pytest.fixture(scope="session")
def audio_files(tmp_path_factory) -> dict[str, Path]:
    """12-секундный тон в WAV/MP3/M4A/FLAC/OGG/OPUS (форматы, которые есть в FFmpeg)."""
    ffmpeg = _ffmpeg()
    folder = tmp_path_factory.mktemp("audio")
    wav = folder / "tone.wav"
    created = _run([
        ffmpeg, "-y", "-f", "lavfi",
        "-i", "sine=frequency=440:sample_rate=44100:duration=12",
        "-ac", "2", "-c:a", "pcm_s16le", str(wav),
    ])
    if created.returncode != 0:
        pytest.skip(f"FFmpeg не смог создать WAV: {created.stderr[-300:]!r}")

    files: dict[str, Path] = {"wav": wav}
    codecs = {
        "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
        "m4a": ["-c:a", "aac", "-b:a", "256k"],
        "flac": ["-c:a", "flac"],
        "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
        "opus": ["-c:a", "libopus", "-b:a", "128k"],
    }
    for ext, codec_args in codecs.items():
        output = folder / f"tone.{ext}"
        result = _run([ffmpeg, "-y", "-i", str(wav), *codec_args, str(output)])
        if result.returncode == 0 and output.exists() and output.stat().st_size:
            files[ext] = output
    return files


@pytest.fixture(scope="session")
def clipped_wav(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Синус, перегруженный на +20 dB — для детектора клиппинга."""
    ffmpeg = _ffmpeg()
    output = tmp_path_factory.mktemp("clipped") / "clipped.wav"
    # sine в FFmpeg имеет амплитуду ~0.088 (−21 dBFS), поэтому +40 dB
    # гарантированно уводит сигнал в клиппинг при конвертации в s16.
    result = _run([
        ffmpeg, "-y", "-f", "lavfi",
        "-i", "sine=frequency=1000:sample_rate=44100:duration=4",
        "-af", "volume=40dB", "-ac", "2", "-c:a", "pcm_s16le", str(output),
    ])
    if result.returncode != 0:
        pytest.skip(f"FFmpeg не смог создать клиппинг: {result.stderr[-300:]!r}")
    return output


@pytest.fixture
async def db_engine():
    """Отдельная in-memory SQLite-база: не трогает data/music.db."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(MusicBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_maker(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def db_session(db_maker):
    async with db_maker() as session:
        yield session


@pytest.fixture
def temp_music_dir(tmp_path, monkeypatch):
    """Подменяет каталог данных музыкальной системы на временный."""
    data_dir = tmp_path / "music-data"
    monkeypatch.setattr(music_settings, "data_dir", data_dir)
    music_settings.ensure_dirs()
    return data_dir
