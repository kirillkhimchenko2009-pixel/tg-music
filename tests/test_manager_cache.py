"""Download Manager: кэш файлов и уборка orphan-обложек."""
from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from music.config import MusicSettings, music_settings
from music.domain import (AudioAnalysis, AudioProbe, AudioSource,
                          CoverAnalysis, CoverCandidate, PermissionInfo,
                          ProviderCapabilities, TrackCandidate)
from music.enums import MediaKind, PermissionStatus, QualityCategory
from music.providers.base import MusicProvider

manager_module = importlib.import_module("music.downloader.manager")


def _analysis() -> AudioAnalysis:
    probe = AudioProbe(codec="mp3", container="mp3", bitrate_kbps=320,
                       sample_rate=44100, duration=12, has_audio=True)
    return AudioAnalysis(probe=probe, category=QualityCategory.HIGH_QUALITY,
                         score=80)


def _cached(tmp_path: Path, **overrides) -> SimpleNamespace:
    path = overrides.pop("path", None)
    if path is None:
        path = tmp_path / "cached.mp3"
        path.write_bytes(b"x" * 256)
    base = dict(
        path=str(path), size=256, format="mp3", media_kind="audio",
        bitrate_kbps=320, sample_rate=44100, duration=12,
        quality_category=QualityCategory.HIGH_QUALITY.value,
        telegram_file_id="FID", telegram_file_unique_id="UNIQ",
        sha256="a" * 64, title="Cached Song", artist="Cached Artist")
    base.update(overrides)
    return SimpleNamespace(**base)


def _candidate(**kwargs) -> TrackCandidate:
    base = dict(provider="local", external_id="track-1", title="Song",
                artist="Artist", album="Album", duration=12,
                permission=PermissionStatus.DOWNLOAD_ALLOWED)
    base.update(kwargs)
    return TrackCandidate(**base)


@pytest.fixture(autouse=True)
def _fast_analysis(monkeypatch):
    async def analyze(path, **kwargs):
        return _analysis()

    monkeypatch.setattr(manager_module, "analyze_audio", analyze)


class TestFromCache:
    async def test_missing_file_and_no_file_id_returns_none(self, tmp_path):
        manager = manager_module.DownloadManager()
        cached = _cached(tmp_path, path=str(tmp_path / "gone.mp3"),
                         telegram_file_id="")
        assert await manager._from_cache(cached, _candidate(), "key") is None

    async def test_missing_file_but_file_id_is_usable(self, tmp_path):
        manager = manager_module.DownloadManager()
        cached = _cached(tmp_path, path=str(tmp_path / "gone.mp3"))
        prepared = await manager._from_cache(cached, _candidate(), "key")

        assert prepared is not None
        assert prepared.path == ""
        assert prepared.cache_hit is True
        assert prepared.telegram_file_id == "FID"
        assert prepared.size == 256
        assert prepared.media_kind is MediaKind.AUDIO

    async def test_no_cache_returns_none(self):
        manager = manager_module.DownloadManager()
        assert await manager._from_cache(None, _candidate(), "key") is None

    async def test_force_document_overrides_cached_audio_kind(self, tmp_path):
        manager = manager_module.DownloadManager()
        candidate = _candidate(extra={"force_document": True})
        prepared = await manager._from_cache(_cached(tmp_path), candidate, "key")

        assert prepared is not None
        assert prepared.media_kind is MediaKind.DOCUMENT

    async def test_file_over_upload_limit_forces_redownload(self, tmp_path):
        big = tmp_path / "big.mp3"
        big.write_bytes(b"x" * (2 * 1024 * 1024))
        manager = manager_module.DownloadManager(
            settings=MusicSettings(max_upload_mb=1))
        cached = _cached(tmp_path, path=str(big), size=big.stat().st_size)
        assert await manager._from_cache(cached, _candidate(), "key") is None

    async def test_file_id_cache_is_allowed_above_limit(self, tmp_path):
        big = tmp_path / "big.mp3"
        big.write_bytes(b"x" * (2 * 1024 * 1024))
        manager = manager_module.DownloadManager(
            settings=MusicSettings(max_upload_mb=1))
        cached = _cached(tmp_path, path=str(big), size=big.stat().st_size)
        # Локального файла нет, но file_id остаётся валидным: лимит не мешает.
        cached.path = ""
        cached.size = 2 * 1024 * 1024
        prepared = await manager._from_cache(cached, _candidate(), "key")
        assert prepared is not None
        assert prepared.path == ""

    async def test_real_file_below_limit_is_returned(self, tmp_path):
        manager = manager_module.DownloadManager()
        prepared = await manager._from_cache(_cached(tmp_path), _candidate(),
                                             "key")

        assert prepared is not None
        assert Path(prepared.path).exists()
        assert prepared.media_kind is MediaKind.AUDIO
        assert prepared.audio.category is QualityCategory.HIGH_QUALITY
        assert prepared.cache_hit is True

    async def test_analysis_failure_drops_cache_entry(self, tmp_path, monkeypatch):
        async def broken(path, **kwargs):
            raise RuntimeError("ffprobe missing")

        monkeypatch.setattr(manager_module, "analyze_audio", broken)
        manager = manager_module.DownloadManager()
        assert await manager._from_cache(_cached(tmp_path), _candidate(),
                                         "key") is None


class LocalProvider(MusicProvider):
    name = "local"
    display_name = "Local"

    def __init__(self, path: Path):
        super().__init__()
        self.capabilities = ProviderCapabilities(search=False, direct_link=False,
                                                 download=True, stream=True)
        self.path = Path(path)

    async def get_download_permissions(self, track):
        return PermissionInfo(status=PermissionStatus.DOWNLOAD_ALLOWED,
                              source_url="https://example.com/official")

    async def get_audio_stream(self, track):
        return AudioSource(url="", filename=self.path.name, mime="audio/mpeg",
                           expected_format="mp3",
                           extra={"local_path": str(self.path)})

    def get_source_url(self, track):
        return "https://example.com/official"


class TestCoverCleanup:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, temp_music_dir):
        monkeypatch.setattr(manager_module, "check_disk_space", lambda: None)
        monkeypatch.setattr(manager_module, "prepare_cover",
                            self._cover)
        monkeypatch.setattr(manager_module, "write_metadata",
                            lambda *a, **k: SimpleNamespace(errors=[]))
        monkeypatch.setattr(manager_module, "audio_format",
                            lambda *a, **k: "mp3")
        # реальный ffprobe здесь не нужен: файл синтетический
        monkeypatch.setattr(manager_module, "finalize", _async_finalize)

    @staticmethod
    async def _cover(candidate, provider, local_path=""):
        return CoverAnalysis(
            candidate=CoverCandidate(data=b"jpeg", mime="image/jpeg",
                                     width=600, height=600,
                                     source="download"),
            accepted=True)

    def _run_args(self, tmp_path):
        source = tmp_path / "source.mp3"
        source.write_bytes(b"ID3" + b"\x00" * 512)
        cover = tmp_path / "orphan-cover.jpg"
        cover.write_bytes(b"jpeg")
        return source, cover

    async def test_cover_removed_when_pipeline_fails_after_cover(
            self, tmp_path, monkeypatch):
        source, cover = self._run_args(tmp_path)
        monkeypatch.setattr(manager_module, "store_cover_copy",
                            lambda *a, **k: str(cover))
        monkeypatch.setattr(manager_module, "media_kind_for",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("boom")))

        manager = manager_module.DownloadManager()
        job_id = uuid.uuid4().hex
        outcome = await manager.prepare(job_id, 1, _candidate(),
                                        provider=LocalProvider(source))

        assert outcome.ok is False
        assert outcome.error_code == "UNEXPECTED"
        assert cover.exists() is False
        assert (music_settings.tmp_dir / job_id).exists() is False

    async def test_cover_kept_on_success(self, tmp_path, monkeypatch):
        source, cover = self._run_args(tmp_path)
        monkeypatch.setattr(manager_module, "store_cover_copy",
                            lambda *a, **k: str(cover))

        manager = manager_module.DownloadManager()
        outcome = await manager.prepare(uuid.uuid4().hex, 1, _candidate(),
                                        provider=LocalProvider(source))

        assert outcome.ok is True
        assert outcome.prepared is not None
        assert outcome.prepared.cover_path == str(cover)
        assert cover.exists() is True


async def _async_finalize(path, *, content_type="", expected_format="",
                          job_id=""):
    return Path(path), _analysis().probe
