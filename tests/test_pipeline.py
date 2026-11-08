"""Пайплайн: проверка файла, Download Manager и пользовательские загрузки."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select

from music.config import music_settings
from music.domain import (AudioSource, PermissionInfo, ProviderCapabilities,
                          TrackCandidate)
from music.downloader.file_handler import (copy_local, finalize,
                                           validate_content_type)
from music.downloader.manager import DownloadManager
from music.enums import PermissionStatus
from music.errors import (InvalidContent, PermissionDenied, ProcessingError,
                          ProviderError)
from music.models import (MusicAlbum, MusicArtist, MusicAudioQualityReport,
                          MusicCachedFile, MusicDownloadHistory, MusicTrack)
from music.providers.base import MusicProvider
from music.providers.user_upload import UserUploadProvider
from music.storage import cleanup_job_dir, new_job_id


class LocalProvider(MusicProvider):
    """Отдаёт локальный файл, не ходит в сеть."""

    name = "local"
    display_name = "Local Test"

    def __init__(self, path: str | Path,
                 permission: PermissionStatus = PermissionStatus.DOWNLOAD_ALLOWED):
        super().__init__()
        self.capabilities = ProviderCapabilities(search=False, direct_link=False,
                                                 download=True, stream=True)
        self.path = str(path)
        self.permission = permission

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        return PermissionInfo(status=self.permission,
                              source_url="https://example.com/official")

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        return AudioSource(url="", filename=Path(self.path).name,
                           mime="audio/mpeg", expected_format="mp3",
                           extra={"local_path": self.path})

    def get_source_url(self, track: TrackCandidate) -> str:
        return "https://example.com/official"


def _candidate(provider: str = "local", **kwargs) -> TrackCandidate:
    base = dict(provider=provider, external_id="1", title="Test Song",
                artist="Test Artist", album="Test Album", duration=12,
                permission=PermissionStatus.DOWNLOAD_ALLOWED)
    base.update(kwargs)
    return TrackCandidate(**base)


class TestFinalize:
    async def test_valid_mp3(self, audio_files, tmp_path: Path):
        source = tmp_path / "track.bin"
        shutil.copy2(audio_files["mp3"], source)
        path, probe = await finalize(source, content_type="audio/mpeg",
                                     job_id=new_job_id())
        assert path.suffix == ".mp3"
        assert path.exists()
        assert probe.has_audio is True
        assert probe.duration == pytest.approx(12, abs=1)

    async def test_extension_is_repaired(self, audio_files, tmp_path: Path):
        source = tmp_path / "wrong.dat"
        shutil.copy2(audio_files["mp3"], source)
        path, _ = await finalize(source, content_type="application/octet-stream",
                                 job_id=new_job_id())
        assert path.suffix == ".mp3"

    async def test_bad_content_type(self, audio_files, tmp_path: Path):
        source = tmp_path / "track.mp3"
        shutil.copy2(audio_files["mp3"], source)
        with pytest.raises(InvalidContent) as exc:
            await finalize(source, content_type="text/html")
        assert exc.value.code == "BAD_CONTENT_TYPE"

    async def test_empty_file(self, tmp_path: Path):
        source = tmp_path / "empty.mp3"
        source.write_bytes(b"")
        with pytest.raises(InvalidContent) as exc:
            await finalize(source)
        assert exc.value.code == "EMPTY_FILE"

    async def test_not_audio(self, tmp_path: Path):
        source = tmp_path / "text.mp3"
        source.write_bytes(b"this is not audio" * 100)
        with pytest.raises(InvalidContent) as exc:
            await finalize(source, content_type="audio/mpeg")
        assert exc.value.code == "NO_AUDIO"

    async def test_missing_file(self, tmp_path: Path):
        with pytest.raises(ProcessingError) as exc:
            await finalize(tmp_path / "missing.mp3")
        assert exc.value.code == "FILE_MISSING"

    def test_content_type_whitelist(self):
        for value in ("audio/mpeg", "audio/flac", "video/mp4",
                      "application/octet-stream", "binary/octet-stream", ""):
            validate_content_type(value)
        with pytest.raises(InvalidContent):
            validate_content_type("application/pdf")

    def test_copy_local(self, audio_files, temp_music_dir: Path):
        dest_dir = temp_music_dir / "copy"
        dest_dir.mkdir()
        copied = copy_local(audio_files["mp3"], dest_dir, job_id="abcdef123456")
        assert copied.exists()
        assert copied.parent == dest_dir
        assert copied.read_bytes() == audio_files["mp3"].read_bytes()

    def test_copy_local_missing(self, tmp_path: Path, temp_music_dir: Path):
        with pytest.raises(ProcessingError):
            copy_local(tmp_path / "missing.mp3", temp_music_dir,
                       job_id="abcdef123456")


class TestDownloadManager:
    async def test_successful_pipeline_without_session(self, audio_files,
                                                       temp_music_dir: Path):
        provider = LocalProvider(audio_files["mp3"])
        manager = DownloadManager()
        job_id = new_job_id()
        outcome = await manager.prepare(job_id, 1, _candidate(),
                                        provider=provider)
        try:
            assert outcome.ok is True
            assert outcome.error_code == ""
            prepared = outcome.prepared
            assert prepared is not None
            assert prepared.file_format == "mp3"
            assert Path(prepared.path).exists()
            assert prepared.duration == pytest.approx(12, abs=1)
            assert prepared.sha256
            # синтетический тон может выглядеть «перекодированным» из-за узкой
            # полосы, поэтому проверяем именно отсутствие повреждения
            assert prepared.audio.is_sendable is True
            assert prepared.audio.category.value != "DAMAGED"
        finally:
            cleanup_job_dir(job_id)

    async def test_pipeline_with_database_records_everything(self, audio_files,
                                                              temp_music_dir: Path,
                                                              db_session):
        provider = LocalProvider(audio_files["mp3"])
        manager = DownloadManager()
        job_id = new_job_id()
        outcome = await manager.prepare(job_id, 2, _candidate(),
                                        provider=provider, session=db_session)
        assert outcome.ok is True
        prepared = outcome.prepared
        assert prepared is not None
        assert Path(prepared.path).exists()
        assert music_settings.cache_dir in Path(prepared.path).parents

        assert (await db_session.execute(
            select(func.count()).select_from(MusicTrack))).scalar() == 1
        assert (await db_session.execute(
            select(func.count()).select_from(MusicArtist))).scalar() == 1
        assert (await db_session.execute(
            select(func.count()).select_from(MusicAlbum))).scalar() == 1
        assert (await db_session.execute(
            select(func.count()).select_from(MusicDownloadHistory))).scalar() == 1
        assert (await db_session.execute(
            select(func.count()).select_from(MusicAudioQualityReport))).scalar() == 1
        assert (await db_session.execute(
            select(func.count()).select_from(MusicCachedFile))).scalar() == 1
        assert prepared.history_id is not None

    async def test_permission_denied_stops_before_download(self, audio_files,
                                                           temp_music_dir: Path):
        provider = LocalProvider(audio_files["mp3"],
                                 permission=PermissionStatus.STREAM_ONLY)
        manager = DownloadManager()
        job_id = new_job_id()
        outcome = await manager.prepare(job_id, 3, _candidate(),
                                        provider=provider)
        assert outcome.ok is False
        assert outcome.error_code == "STREAM_ONLY"
        assert outcome.official_url == "https://example.com/official"
        assert outcome.permission is not None
        assert outcome.permission.status is PermissionStatus.STREAM_ONLY
        assert not (music_settings.tmp_dir / job_id).exists()

    async def test_corrupt_file_is_rejected(self, tmp_path: Path,
                                            temp_music_dir: Path):
        source = tmp_path / "broken.mp3"
        source.write_bytes(b"not audio" * 1000)
        provider = LocalProvider(source)
        manager = DownloadManager()
        job_id = new_job_id()
        outcome = await manager.prepare(job_id, 4, _candidate(),
                                        provider=provider)
        assert outcome.ok is False
        assert outcome.error_code == "NO_AUDIO"
        assert outcome.retryable is False

    async def test_unknown_provider(self, audio_files, temp_music_dir: Path):
        manager = DownloadManager()
        outcome = await manager.prepare(new_job_id(), 5,
                                        _candidate(provider="missing"))
        assert outcome.ok is False
        assert outcome.error_code == "PROVIDER_UNKNOWN"

    async def test_cancel_before_download(self, audio_files, temp_music_dir: Path):
        import asyncio
        provider = LocalProvider(audio_files["mp3"])
        event = asyncio.Event()
        event.set()
        outcome = await DownloadManager().prepare(new_job_id(), 6, _candidate(),
                                                  provider=provider, cancel=event)
        assert outcome.ok is False
        assert outcome.error_code == "CANCELLED"


class TestUserUploadProvider:
    async def test_describe_and_permissions(self, audio_files, temp_music_dir: Path):
        target = music_settings.tmp_dir / "My Artist - My Song.mp3"
        shutil.copy2(audio_files["mp3"], target)
        provider = UserUploadProvider()
        candidate = provider.describe(str(target))
        assert candidate.provider == "user"
        assert candidate.title == "My Song"
        assert candidate.artist == "My Artist"
        assert candidate.permission is PermissionStatus.DOWNLOAD_ALLOWED
        assert candidate.extra["local_path"] == str(target)

        info = await provider.get_download_permissions(candidate)
        assert info.status is PermissionStatus.DOWNLOAD_ALLOWED

        source = await provider.get_audio_stream(candidate)
        assert source.extra["local_path"] == str(target)
        assert source.expected_format == "mp3"

    async def test_outside_root_is_blocked(self, tmp_path: Path,
                                           temp_music_dir: Path):
        outside = tmp_path / "outside.mp3"
        outside.write_bytes(b"ID3data")
        provider = UserUploadProvider()
        with pytest.raises(PermissionDenied):
            provider.describe(str(outside))

    async def test_missing_file(self, temp_music_dir: Path):
        provider = UserUploadProvider()
        with pytest.raises(ProviderError):
            provider.describe(str(music_settings.tmp_dir / "missing.mp3"))

    async def test_unsupported_format(self, temp_music_dir: Path):
        target = music_settings.tmp_dir / "file.txt"
        target.write_text("text")
        provider = UserUploadProvider()
        with pytest.raises(PermissionDenied):
            provider.describe(str(target))
