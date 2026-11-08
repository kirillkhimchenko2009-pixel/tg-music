"""Metadata Engine на Mutagen: запись тегов и обложек без перекодирования."""
from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
from PIL import Image

from music.processing.metadata_editor import (MetadataFields, detect_image_mime,
                                              embedded_cover, read_metadata,
                                              write_metadata)
from music.quality.analyzer import analyze_metadata_only, probe_file
from music.quality.cover_checker import has_embedded_cover
from music.quality.metadata_checker import inspect


@pytest.fixture(scope="module")
def cover_bytes() -> bytes:
    image = Image.new("RGB", (600, 600), (25, 90, 160))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _fields(**kwargs) -> MetadataFields:
    base = dict(title="Test Song", artist="Test Artist", album="Test Album",
                album_artist="Test Artist", track_number=3, track_total=12,
                disc_number=1, disc_total=1, year=2021, genre="Electronic",
                composer="Composer Name", isrc="USRC17607839")
    base.update(kwargs)
    return MetadataFields(**base)


def _report(path: Path):
    return inspect(read_metadata(path), has_cover=has_embedded_cover(path))


class TestDetectImageMime:
    @pytest.mark.parametrize("head,mime", [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
        (b"GIF89a", "image/gif"),
        (b"unknown", "image/jpeg"),
    ])
    def test_detect(self, head, mime):
        assert detect_image_mime(head) == mime


class TestMetadataFields:
    def test_from_candidate(self, cover_bytes):
        from music.domain import TrackCandidate
        candidate = TrackCandidate(provider="jamendo", external_id="1",
                                   title="Song", artist="Artist", album="Album",
                                   year=2020, isrc="ABC", album_artist="Various")
        fields = MetadataFields.from_candidate(candidate)
        assert fields.title == "Song"
        assert fields.album_artist == "Various"
        assert fields.year == 2020
        assert fields.isrc == "ABC"

    def test_year_from_tags(self):
        from music.domain import TrackCandidate
        candidate = TrackCandidate(provider="x", external_id="1", title="Song")
        fields = MetadataFields.from_candidate(candidate, {"date": "1998-01-01"})
        assert fields.year == 1998

    def test_as_dict_skips_empty(self):
        fields = MetadataFields(title="Song")
        assert fields.as_dict() == {"title": "Song"}


class TestWriteMetadata:
    @pytest.mark.parametrize("ext", ["mp3", "m4a", "flac", "ogg", "opus", "wav"])
    async def test_roundtrip_each_format(self, audio_files, temp_music_dir: Path,
                                         cover_bytes, ext):
        if ext not in audio_files:
            pytest.skip(f"нет синтетического {ext}")
        path = temp_music_dir / f"meta.{ext}"
        shutil.copy2(audio_files[ext], path)
        probe_before = await probe_file(path)

        result = write_metadata(path, _fields(), cover=cover_bytes)
        assert result.ok, result.errors
        assert "title" in result.changed
        assert result.cover_embedded is True

        report = _report(path)
        assert report.title == "Test Song"
        assert report.artist == "Test Artist"
        assert report.album == "Test Album"
        assert report.year == 2021
        assert report.isrc == "USRC17607839"

        cover = embedded_cover(path)
        assert cover is not None
        data, mime = cover
        assert data == cover_bytes
        assert mime.startswith("image/")

        probe_after = await probe_file(path)
        assert probe_after.has_audio is True
        assert probe_after.codec == probe_before.codec
        assert probe_after.duration == pytest.approx(probe_before.duration, abs=0.2)

    async def test_analyze_metadata_only_sees_written_tags(self, audio_files,
                                                           temp_music_dir: Path,
                                                           cover_bytes):
        path = temp_music_dir / "report.mp3"
        shutil.copy2(audio_files["mp3"], path)
        write_metadata(path, _fields(), cover=cover_bytes)
        report = await analyze_metadata_only(path)
        assert report.title == "Test Song"
        assert report.has_cover is True

    async def test_does_not_overwrite_good_values(self, audio_files,
                                                  temp_music_dir: Path):
        path = temp_music_dir / "keep.mp3"
        shutil.copy2(audio_files["mp3"], path)
        write_metadata(path, _fields(), overwrite=True)
        second = write_metadata(path, _fields(title="Other Title"),
                                overwrite=False)
        assert "title" not in second.changed
        assert _report(path).title == "Test Song"

    async def test_overwrite_true_replaces(self, audio_files, temp_music_dir: Path):
        path = temp_music_dir / "replace.mp3"
        shutil.copy2(audio_files["mp3"], path)
        write_metadata(path, _fields(), overwrite=True)
        second = write_metadata(path, _fields(title="Other Title"),
                                overwrite=True)
        assert second.changed.get("title") == "Other Title"
        assert _report(path).title == "Other Title"

    async def test_replaces_junk_title(self, audio_files, temp_music_dir: Path):
        path = temp_music_dir / "junk.mp3"
        shutil.copy2(audio_files["mp3"], path)
        write_metadata(path, _fields(title="Song (Official Video)"), overwrite=True)
        second = write_metadata(path, _fields(title="Song"), overwrite=False)
        assert second.changed.get("title") == "Song"
        assert _report(path).title == "Song"

    def test_corrupted_file_reports_error(self, tmp_path: Path):
        path = tmp_path / "broken.mp3"
        path.write_bytes(b"not audio")
        result = write_metadata(path, _fields())
        assert result.ok is False
        assert result.errors

    def test_unknown_extension_reports_error(self, tmp_path: Path):
        path = tmp_path / "file.xyz"
        path.write_bytes(b"whatever")
        result = write_metadata(path, _fields())
        assert result.ok is False

    async def test_embedded_cover_missing(self, audio_files, tmp_path: Path):
        path = tmp_path / "plain.mp3"
        shutil.copy2(audio_files["mp3"], path)
        assert embedded_cover(path) is None
        assert has_embedded_cover(path) is False

    def test_read_metadata_missing_file(self, tmp_path: Path):
        assert read_metadata(tmp_path / "none.mp3") == {}

    async def test_cover_not_written_when_absent(self, audio_files, tmp_path: Path):
        path = tmp_path / "nocover.mp3"
        shutil.copy2(audio_files["mp3"], path)
        result = write_metadata(path, _fields())
        assert result.cover_embedded is False
        assert embedded_cover(path) is None
