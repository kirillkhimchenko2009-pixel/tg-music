"""Репозиторий: пользователи, треки, задания, история, отчёты, избранное."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from music import repository
from music.config import music_settings
from music.domain import (AudioAnalysis, AudioProbe, CoverAnalysis,
                          CoverCandidate, MediaKind, PreparedTrack,
                          TrackCandidate)
from music.enums import JobStatus, PermissionStatus, QualityCategory
from music.models import (MusicAlbum, MusicArtist, MusicAudioQualityReport,
                          MusicCachedFile, MusicCoverQualityReport,
                          MusicDownloadHistory, MusicDownloadJob, MusicSource,
                          MusicTrack, MusicUser, utcnow)


def _candidate(**kwargs) -> TrackCandidate:
    base = dict(provider="jamendo", external_id="track-1",
                title="One More Time", artist="Daft Punk",
                album="Discovery", duration=320, year=2001,
                source_url="https://example.com/track-1",
                cover_url="https://example.com/cover.jpg",
                permission=PermissionStatus.DOWNLOAD_ALLOWED,
                license_name="CC BY", license_url="https://creativecommons.org/licenses/by/4.0/",
                isrc="USRC17607839", file_format="mp3", bitrate_kbps=320)
    base.update(kwargs)
    return TrackCandidate(**base)


def _prepared(candidate: TrackCandidate | None = None, **kwargs) -> PreparedTrack:
    candidate = candidate or _candidate()
    probe = AudioProbe(codec="mp3", container="mp3", bitrate_kbps=320,
                       sample_rate=44100, channels=2, duration=320, size=5_000_000,
                       has_audio=True)
    analysis = AudioAnalysis(probe=probe, category=QualityCategory.HIGH_QUALITY,
                             score=82, clipping=False)
    cover = CoverAnalysis(
        candidate=CoverCandidate(data=b"\x89PNG\r\n\x1a\nxx", width=1000, height=1000,
                                 format="PNG", mime="image/png", size=12,
                                 url="https://example.com/cover.png"),
        accepted=True, sharpness=120.0, blockiness=0.1, score=95)
    base = dict(path="C:/tmp/track.mp3", media_kind=MediaKind.AUDIO,
                mime_type="audio/mpeg", file_format="mp3", size=5_000_000,
                duration=320, title=candidate.title, artist=candidate.artist,
                album=candidate.album, candidate=candidate, audio=analysis,
                cover=cover, cache_key="cache-key-1", sha256="a" * 64)
    base.update(kwargs)
    return PreparedTrack(**base)


class TestUsers:
    async def test_ensure_user_creates_and_updates(self, db_session):
        user = await repository.ensure_user(db_session, 100, "alice", "Alice")
        assert user.user_id == 100
        assert user.username == "alice"
        assert user.first_name == "Alice"

        same = await repository.ensure_user(db_session, 100, "bob", "Bob")
        assert same.user_id == 100
        assert same.username == "bob"
        assert same.first_name == "Bob"
        assert await db_session.get(MusicUser, 100) is not None

    async def test_user_settings_defaults_and_update(self, db_session):
        settings = await repository.get_user_settings(db_session, 200)
        assert settings.send_mode == "auto"
        assert settings.auto_text is False

        updated = await repository.update_user_settings(
            db_session, 200, send_mode="document", auto_text=True,
            default_quality="flac", unknown_field="ignored")
        assert updated.send_mode == "document"
        assert updated.auto_text is True
        assert updated.default_quality == "flac"


class TestTracks:
    async def test_upsert_track_creates_artist_and_album(self, db_session):
        candidate = _candidate()
        row = await repository.upsert_track(db_session, candidate)
        assert row.id is not None
        assert candidate.track_id == row.id
        assert row.title == "One More Time"
        assert row.artist_name == "Daft Punk"
        assert row.album_name == "Discovery"
        assert row.identity_key == candidate.identity_key

        artists = (await db_session.execute(select(MusicArtist))).scalars().all()
        albums = (await db_session.execute(select(MusicAlbum))).scalars().all()
        assert len(artists) == 1
        assert len(albums) == 1
        assert albums[0].artist_id == artists[0].id

    async def test_upsert_track_is_idempotent(self, db_session):
        first = await repository.upsert_track(db_session, _candidate())
        second = await repository.upsert_track(
            db_session, _candidate(title="One More Time (Remastered)",
                                   album="Discovery (Remaster)"))
        assert first.id == second.id
        assert second.title == "One More Time (Remastered)"
        assert (await db_session.execute(
            select(func.count()).select_from(MusicTrack))).scalar() == 1
        assert (await db_session.execute(
            select(func.count()).select_from(MusicArtist))).scalar() == 1

    async def test_upsert_track_without_artist_or_album(self, db_session):
        row = await repository.upsert_track(
            db_session, _candidate(artist="", album="", title="Unknown"))
        assert row.artist_id is None
        assert row.album_id is None
        assert row.title == "Unknown"


class TestJobs:
    async def test_record_update_and_count_jobs(self, db_session):
        candidate = _candidate()
        job = await repository.record_job(db_session, "job-1", 300, candidate)
        assert job.status == JobStatus.QUEUED.value
        assert job.provider == "jamendo"
        assert await repository.count_active_jobs(db_session, 300) == 1

        await repository.update_job(db_session, "job-1",
                                    status=JobStatus.RUNNING, progress=50)
        row = await db_session.get(MusicDownloadJob, "job-1")
        assert row.status == JobStatus.RUNNING.value
        assert row.progress == 50
        assert await repository.count_active_jobs(db_session, 300) == 1

        await repository.update_job(db_session, "job-1",
                                    status=JobStatus.COMPLETED)
        assert await repository.count_active_jobs(db_session, 300) == 0

    async def test_record_job_is_idempotent(self, db_session):
        await repository.record_job(db_session, "job-2", 301, _candidate())
        await repository.record_job(db_session, "job-2", 301,
                                    _candidate(provider="archive", external_id="2"),
                                    status=JobStatus.RUNNING)
        row = await db_session.get(MusicDownloadJob, "job-2")
        assert row.status == JobStatus.RUNNING.value
        assert row.provider == "archive"

    async def test_job_failure_sets_finished_at(self, db_session):
        await repository.record_job(db_session, "job-3", 302, _candidate())
        await repository.record_job_failure(db_session, "job-3", "boom" * 1000)
        row = await db_session.get(MusicDownloadJob, "job-3")
        assert row.status == JobStatus.FAILED.value
        assert len(row.error) <= 2000
        assert row.started_at is not None
        assert row.finished_at is not None

    async def test_update_missing_job_is_noop(self, db_session):
        await repository.update_job(db_session, "missing", status=JobStatus.FAILED)
        await repository.record_job_failure(db_session, "missing", "x")


class TestHistoryAndReports:
    async def test_record_history_and_reports(self, db_session):
        candidate = _candidate()
        track = await repository.upsert_track(db_session, candidate)
        prepared = _prepared(candidate)

        history = await repository.record_history(
            db_session, prepared, user_id=400, job_id="job-4",
            track_id=track.id, permission=PermissionStatus.DOWNLOAD_ALLOWED)
        assert history.id is not None
        assert prepared.history_id == history.id
        assert history.title == candidate.title
        assert history.quality_category == QualityCategory.HIGH_QUALITY.value
        assert history.cache_key == "cache-key-1"

        audio_report = await repository.record_audio_report(
            db_session, prepared, track.id)
        assert audio_report.codec == "mp3"
        assert audio_report.category == QualityCategory.HIGH_QUALITY.value
        assert audio_report.score == 82

        cover_report = await repository.record_cover_report(
            db_session, prepared, track.id)
        assert cover_report is not None
        assert cover_report.width == 1000
        assert cover_report.accepted is True

        await repository.update_history_telegram(db_session, history.id,
                                                 "FILEID", "UNIQUE")
        await db_session.refresh(history)
        assert history.telegram_file_id == "FILEID"
        assert history.telegram_file_unique_id == "UNIQUE"

    async def test_cover_report_skips_invalid_cover(self, db_session):
        prepared = _prepared(cover=None)
        assert await repository.record_cover_report(db_session, prepared, None) is None

    async def test_count_daily_downloads(self, db_session):
        prepared = _prepared()
        await repository.record_history(
            db_session, prepared, user_id=401, job_id="job-5",
            track_id=None, permission=PermissionStatus.DOWNLOAD_ALLOWED)
        assert await repository.count_daily_downloads(db_session, 401) == 1
        assert await repository.count_daily_downloads(db_session, 402) == 0

        row = (await db_session.execute(select(MusicDownloadHistory))).scalars().first()
        row.downloaded_at = utcnow() - timedelta(days=2)
        await db_session.flush()
        assert await repository.count_daily_downloads(db_session, 401) == 0

    async def test_list_history_newest_first(self, db_session):
        for index in range(3):
            prepared = _prepared(cache_key=f"key-{index}")
            await repository.record_history(
                db_session, prepared, user_id=403, job_id=f"job-{index}",
                track_id=None, permission=PermissionStatus.DOWNLOAD_ALLOWED)
            await db_session.flush()
        history = await repository.list_history(db_session, 403, limit=2)
        assert len(history) == 2
        assert history[0].job_id == "job-2"

    async def test_history_for_cache_key(self, db_session):
        prepared = _prepared(cache_key="unique-key")
        await repository.record_history(
            db_session, prepared, user_id=404, job_id="job-6",
            track_id=None, permission=PermissionStatus.DOWNLOAD_ALLOWED)
        found = await repository.history_for_cache_key(db_session, "unique-key")
        assert found is not None
        assert found.job_id == "job-6"
        assert await repository.history_for_cache_key(db_session, "other") is None


class TestFavorites:
    async def test_add_list_remove(self, db_session):
        candidate = _candidate()
        track = await repository.upsert_track(db_session, candidate)
        assert await repository.add_favorite(db_session, 500, track.id) is True
        assert await repository.add_favorite(db_session, 500, track.id) is False

        favorites = await repository.list_favorites(db_session, 500)
        assert len(favorites) == 1
        favorite, loaded = favorites[0]
        assert favorite.track_id == track.id
        assert loaded.title == candidate.title

        assert await repository.remove_favorite(db_session, 500, track.id) is True
        assert await repository.remove_favorite(db_session, 500, track.id) is False
        assert await repository.list_favorites(db_session, 500) == []


class TestSources:
    async def test_upsert_source_create_and_update(self, db_session):
        created = await repository.upsert_source(
            db_session, "jamendo", "Jamendo", {"download": True}, "ok")
        assert created.provider == "jamendo"
        assert created.enabled is True

        updated = await repository.upsert_source(
            db_session, "jamendo", "Jamendo Music", {"download": False}, "down")
        assert updated.id == created.id
        assert updated.display_name == "Jamendo Music"
        assert updated.health == "down"
        assert (await db_session.execute(
            select(func.count()).select_from(MusicSource))).scalar() == 1


class TestCredentials:
    async def test_set_get_and_encrypt(self, db_session, monkeypatch):
        monkeypatch.setattr(music_settings, "credential_key", "unit-test-key")
        await repository.set_credential(db_session, "jamendo", "client_id",
                                        "super-secret")
        plain = await repository.get_credential(db_session, "jamendo", "client_id")
        assert plain == "super-secret"

        from music.models import MusicApiCredential
        row = (await db_session.execute(select(MusicApiCredential))).scalars().first()
        assert row.encrypted_value != "super-secret"
        assert "super-secret" not in row.encrypted_value

    async def test_update_credential(self, db_session, monkeypatch):
        monkeypatch.setattr(music_settings, "credential_key", "unit-test-key")
        await repository.set_credential(db_session, "spotify", "token", "one")
        await repository.set_credential(db_session, "spotify", "token", "two")
        assert await repository.get_credential(db_session, "spotify", "token") == "two"

    async def test_wrong_key_returns_empty(self, db_session, monkeypatch):
        monkeypatch.setattr(music_settings, "credential_key", "key-one")
        await repository.set_credential(db_session, "spotify", "secret", "value")
        monkeypatch.setattr(music_settings, "credential_key", "key-two")
        assert await repository.get_credential(db_session, "spotify", "secret") == ""

    async def test_missing_credential(self, db_session):
        assert await repository.get_credential(db_session, "none", "none") == ""
