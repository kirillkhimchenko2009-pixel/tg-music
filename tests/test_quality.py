"""Audio Quality Engine: FFprobe, клиппинг, категории, метаданные."""
from __future__ import annotations

import pytest

from music.domain import AudioProbe
from music.enums import QualityCategory
from music.quality.analyzer import analyze_audio, probe_file
from music.quality.bitrate_checker import assess, bitrate_tier, is_lossless, is_lossy
from music.quality.clipping_detector import ClipStats, scan_pcm
from music.quality.metadata_checker import inspect
from music.quality.validator import categorize, structural_issues


def _probe(**kwargs) -> AudioProbe:
    base = dict(codec="mp3", container="mp3", bitrate_kbps=320, sample_rate=44100,
                channels=2, duration=200, size=5_000_000, has_audio=True)
    base.update(kwargs)
    return AudioProbe(**base)


class TestParseProbe:
    def test_mp3(self):
        probe = _probe
        assert probe().is_lossless is False

    def test_lossless_helper(self):
        assert is_lossless("flac") is True
        assert is_lossless("pcm_s24le") is True
        assert is_lossless("mp3") is False
        assert is_lossy("aac") is True
        assert is_lossy("flac") is False

    @pytest.mark.parametrize("codec,bitrate,tier", [
        ("mp3", 320, "high"), ("mp3", 192, "standard"), ("mp3", 96, "low"),
        ("mp3", None, "unknown"), ("opus", 160, "high"), ("opus", 96, "standard"),
        ("vorbis", 224, "high"), ("vorbis", 100, "low"),
    ])
    def test_bitrate_tier(self, codec, bitrate, tier):
        assert bitrate_tier(codec, bitrate) == tier

    def test_assess_transcode_detection(self):
        info = assess("mp3", 320, 44100, 16000, duration=60)
        assert info.likely_transcoded is True
        assert "полоса" in info.reason

    def test_assess_lossless_ok(self):
        info = assess("flac", 900, 44100, 22000, duration=60, lossless=True)
        assert info.likely_transcoded is False

    def test_assess_short_track_skipped(self):
        info = assess("mp3", 320, 44100, 8000, duration=5)
        assert info.likely_transcoded is False


class TestProbeFile:
    @pytest.mark.parametrize("ext,codec", [
        ("mp3", "mp3"), ("m4a", "aac"), ("flac", "flac"),
        ("wav", "pcm_s16le"), ("ogg", "vorbis"),
    ])
    async def test_probe_synthetic(self, audio_files, ext, codec):
        if ext not in audio_files:
            pytest.skip(f"нет синтетического {ext}")
        probe = await probe_file(audio_files[ext])
        assert probe.has_audio is True
        assert probe.duration == pytest.approx(12, abs=1.0)
        assert probe.codec == codec

    async def test_missing_file(self, tmp_path):
        from music.errors import ProcessingError
        with pytest.raises(ProcessingError):
            await probe_file(tmp_path / "missing.mp3")

    async def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.mp3"
        path.write_bytes(b"")
        probe = await probe_file(path)
        assert probe.size == 0
        assert probe.has_audio is False
        assert probe.ffprobe_error

    async def test_not_audio(self, tmp_path):
        path = tmp_path / "text.bin"
        path.write_bytes(b"this is definitely not audio" * 100)
        probe = await probe_file(path)
        assert probe.has_audio is False
        assert probe.ffprobe_error


class TestAnalyzeAudio:
    async def test_mp3_high_quality(self, audio_files):
        analysis = await analyze_audio(audio_files["mp3"], deep=False)
        assert analysis.probe.has_audio
        assert analysis.category is QualityCategory.HIGH_QUALITY
        assert analysis.is_sendable is True

    async def test_flac_lossless(self, audio_files):
        if "flac" not in audio_files:
            pytest.skip("нет flac")
        analysis = await analyze_audio(audio_files["flac"], deep=False)
        assert analysis.category is QualityCategory.LOSSLESS_SOURCE
        assert analysis.score >= 90

    async def test_wav_lossless(self, audio_files):
        analysis = await analyze_audio(audio_files["wav"], deep=False)
        assert analysis.category is QualityCategory.LOSSLESS_SOURCE

    async def test_missing_metadata_is_issue_not_damage(self, audio_files):
        analysis = await analyze_audio(audio_files["mp3"], deep=False)
        assert "metadata" in analysis.issue_codes
        assert analysis.category is not QualityCategory.DAMAGED

    async def test_deep_analysis_runs(self, audio_files):
        if "flac" not in audio_files:
            pytest.skip("нет flac")
        analysis = await analyze_audio(audio_files["flac"], deep=True)
        assert analysis.category in (QualityCategory.LOSSLESS_SOURCE,
                                     QualityCategory.HIGH_QUALITY)
        assert analysis.peak_dbfs is not None
        assert analysis.decoded_samples if hasattr(analysis, "decoded_samples") else True


class TestClipping:
    async def test_clean_tone_not_clipped(self, audio_files):
        stats = await scan_pcm(str(audio_files["wav"]))
        assert stats.decode_failed is False
        assert stats.decoded_samples > 0
        assert stats.peak_dbfs is not None

    async def test_clipped_tone_detected(self, clipped_wav):
        stats = await scan_pcm(str(clipped_wav))
        assert stats.decode_failed is False
        assert stats.clipping is True
        assert stats.clipped_samples > 0
        assert stats.long_runs > 0
        assert stats.peak_dbfs is not None and stats.peak_dbfs <= 0.1

    async def test_missing_file_does_not_raise(self, tmp_path):
        stats = await scan_pcm(str(tmp_path / "missing.wav"))
        assert stats.decode_failed is True or stats.decoded_samples == 0


class TestCategorize:
    def test_structural_issues(self):
        probe = AudioProbe(codec="mp3", has_audio=False, duration=0, size=0)
        codes = [i.code for i in structural_issues(probe)]
        assert "no_audio" in codes
        assert "no_duration" in codes

    def test_damaged_on_probe_error(self):
        probe = _probe(has_audio=False, ffprobe_error="broken")
        category, score, issues = categorize(probe, ClipStats())
        assert category is QualityCategory.DAMAGED
        assert score <= 10
        assert any(i.severity == "error" for i in issues)

    def test_lossless_category(self):
        probe = _probe(codec="flac", container="flac", bitrate_kbps=900)
        category, score, _ = categorize(probe, ClipStats(peak_dbfs=-1.0))
        assert category is QualityCategory.LOSSLESS_SOURCE
        assert score >= 90

    def test_high_quality_lossy(self):
        probe = _probe(codec="mp3", bitrate_kbps=320)
        category, _, _ = categorize(probe, ClipStats(peak_dbfs=-1.0))
        assert category is QualityCategory.HIGH_QUALITY

    def test_standard_quality_lossy(self):
        probe = _probe(codec="mp3", bitrate_kbps=192)
        category, _, _ = categorize(probe, ClipStats(peak_dbfs=-1.0))
        assert category is QualityCategory.STANDARD_QUALITY

    def test_low_quality_lossy(self):
        probe = _probe(codec="mp3", bitrate_kbps=96)
        category, _, _ = categorize(probe, ClipStats(peak_dbfs=-1.0))
        assert category is QualityCategory.LOW_QUALITY

    def test_clipping_warning_keeps_category(self):
        probe = _probe(codec="mp3", bitrate_kbps=320)
        clip = ClipStats(clipping=True, clipped_samples=100, clipped_ratio=0.0005,
                         long_runs=1, peak_dbfs=-0.0)
        category, score, issues = categorize(probe, clip)
        assert category is QualityCategory.HIGH_QUALITY
        assert any(i.code == "clipping" and i.severity == "warning" for i in issues)
        assert score < 82

    def test_heavy_clipping_damages(self):
        probe = _probe(codec="mp3", bitrate_kbps=320)
        clip = ClipStats(clipping=True, clipped_samples=10000,
                         clipped_ratio=0.05, long_runs=500, peak_dbfs=0.0)
        category, score, _ = categorize(probe, clip)
        assert category is QualityCategory.DAMAGED
        assert score <= 10

    def test_silence_warning(self):
        probe = _probe(duration=200)
        category, score, issues = categorize(probe, ClipStats(),
                                             silence_total=60)
        assert any(i.code == "silence" for i in issues)
        assert category is QualityCategory.HIGH_QUALITY

    def test_low_sample_rate(self):
        probe = _probe(sample_rate=22050)
        _, _, issues = categorize(probe, ClipStats())
        assert any(i.code == "low_sample_rate" for i in issues)

    def test_low_sample_rate_lossless_downgraded(self):
        probe = _probe(codec="flac", container="flac", bitrate_kbps=900,
                       sample_rate=22050)
        category, _, _ = categorize(probe, ClipStats())
        assert category is QualityCategory.STANDARD_QUALITY

    def test_duration_mismatch(self):
        probe = _probe(duration=200)
        from music.quality.metadata_checker import MetadataReport
        meta = MetadataReport(title="Song", artist="Artist", album="Album")
        _, _, issues = categorize(probe, ClipStats(), metadata=meta,
                                  expected_duration=300)
        assert any(i.code == "duration_mismatch" for i in issues)


class TestMetadataChecker:
    def test_complete_tags(self):
        report = inspect({"title": "Song", "artist": "Artist", "album": "Album",
                          "date": "2020-05-01", "isrc": "USRC17607839"},
                         has_cover=True)
        assert report.complete is True
        assert report.year == 2020
        assert report.isrc == "USRC17607839"
        assert report.issues == []

    def test_missing_fields(self):
        report = inspect({})
        assert report.complete is False
        assert "нет тега Title" in report.issues
        assert "нет тега Artist" in report.issues
        assert "нет тега Album" in report.issues
        assert "нет встроенной обложки" in report.issues

    def test_junk_title_suspicious(self):
        report = inspect({"title": "Song (Official Video)", "artist": "A",
                          "album": "B"}, has_cover=True)
        assert report.suspicious is True
        assert any("мусор" in i for i in report.issues)

    def test_filename_title_match(self):
        report = inspect({"title": "track", "artist": "A", "album": "B"},
                         filename="track.mp3", has_cover=True)
        assert report.suspicious is True
        assert any("именем файла" in i for i in report.issues)

    def test_expected_duration_mismatch(self):
        report = inspect({"title": "Song", "artist": "A", "album": "B"},
                         expected_duration=200, actual_duration=300, has_cover=True)
        assert report.suspicious is True
        assert any("Длительность" in i for i in report.issues)

    def test_frame_names_mp3(self):
        report = inspect({"TIT2": "Song", "TPE1": "Artist", "TALB": "Album",
                          "TDRC": "1999", "TSRC": "ABC123"},
                         has_cover=True)
        assert report.title == "Song"
        assert report.artist == "Artist"
        assert report.year == 1999
        assert report.isrc == "ABC123"
