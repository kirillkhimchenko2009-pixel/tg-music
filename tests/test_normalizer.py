"""Нормализация поиска: версии, ссылки, похожесть, длительности."""
from __future__ import annotations

import pytest

from music.domain import TrackCandidate
from music.enums import PermissionStatus, TrackVersion
from music.search.normalizer import (clean_title, detect_version, duration_score,
                                     normalize_text, parse, parse_duration,
                                     score_candidate, similarity)


class TestNormalizeText:
    def test_lowercase_and_punctuation(self):
        assert normalize_text("  Hello,   WORLD!  ") == "hello world"

    def test_yo_and_accents(self):
        assert normalize_text("Ёлка & Café") == "елка and cafe"

    def test_empty(self):
        assert normalize_text("") == ""
        assert normalize_text(None) == ""  # type: ignore[arg-type]

    def test_unicode_letters_preserved(self):
        assert "музыка" in normalize_text("Музыка")


class TestDetectVersion:
    @pytest.mark.parametrize("text,expected", [
        ("Song (Remix)", TrackVersion.REMIX),
        ("Song - Live at Wembley", TrackVersion.LIVE),
        ("Song (Acoustic Version)", TrackVersion.ACOUSTIC),
        ("Song (Instrumental)", TrackVersion.INSTRUMENTAL),
        ("Song (Sped Up)", TrackVersion.SPEED),
        ("Song (Nightcore)", TrackVersion.SPEED),
        ("Song (Extended Mix)", TrackVersion.EXTENDED),
        ("Song (Radio Edit)", TrackVersion.RADIO_EDIT),
        ("Song (Demo)", TrackVersion.DEMO),
        ("Song (Cover)", TrackVersion.COVER),
        ("Just a Song", TrackVersion.ORIGINAL),
    ])
    def test_versions(self, text, expected):
        assert detect_version(text)[0] is expected

    def test_version_detail_keeps_brackets(self):
        version, detail = detect_version("Track (Live in Berlin)")
        assert version is TrackVersion.LIVE
        assert detail == "Live in Berlin"


class TestCleanTitle:
    def test_removes_official_video(self):
        assert clean_title("Song (Official Music Video)") == "Song"

    def test_removes_feat(self):
        cleaned = clean_title("Song (feat. Somebody)")
        assert cleaned == "Song"

    def test_removes_version_suffix(self):
        assert clean_title("Song - Remix") == "Song"

    def test_keeps_normal_brackets(self):
        assert clean_title("Song (Part 2)") == "Song (Part 2)"

    def test_empty_input(self):
        assert clean_title("") == ""


class TestParse:
    def test_artist_dash_title(self):
        q = parse("Daft Punk - One More Time")
        assert q.artist == "Daft Punk"
        assert q.title == "One More Time"
        assert q.version is TrackVersion.ORIGINAL
        assert q.search_text == "Daft Punk One More Time"

    def test_en_dash(self):
        q = parse("Daft Punk – One More Time")
        assert q.artist == "Daft Punk"
        assert q.title == "One More Time"

    def test_remix_version(self):
        q = parse("Daft Punk - One More Time (Remix)")
        assert q.artist == "Daft Punk"
        assert q.title == "One More Time"
        assert q.version is TrackVersion.REMIX

    def test_title_by_artist(self):
        q = parse("One More Time by Daft Punk")
        assert q.title == "One More Time"
        assert q.artist == "Daft Punk"

    def test_feat_collected(self):
        q = parse("Song feat. Alice, Bob")
        assert "Alice" in q.feat
        assert "Bob" in q.feat

    def test_year(self):
        q = parse("Song 1998 remaster")
        assert q.year == 1998

    def test_file_extension_removed(self):
        q = parse("track.mp3")
        assert q.title == "track"

    def test_empty(self):
        q = parse("")
        assert q.raw == ""
        assert q.is_url is False

    @pytest.mark.parametrize("url,provider,external", [
        ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", "spotify",
         "4uLU6hMCjMI75M1A2tKUQC"),
        ("https://archive.org/details/gd1977-05-08", "archive", "gd1977-05-08"),
        ("https://www.jamendo.com/track/123456/song", "jamendo", "123456"),
        ("https://artist.bandcamp.com/track/song", "bandcamp",
         "https://artist.bandcamp.com/track/song"),
        ("https://soundcloud.com/artist/song", "soundcloud",
         "https://soundcloud.com/artist/song"),
    ])
    def test_url_hints(self, url, provider, external):
        q = parse(f"look: {url}")
        assert q.is_url is True
        assert q.provider_hint == provider
        assert q.external_id_hint == external

    def test_unknown_url_still_url(self):
        q = parse("https://example.com/song")
        assert q.is_url is True
        assert q.provider_hint == ""
        assert q.source_url == "https://example.com/song"

    def test_url_trailing_punctuation(self):
        q = parse("(https://open.spotify.com/track/abc123).")
        assert q.source_url == "https://open.spotify.com/track/abc123"


class TestSimilarity:
    def test_identical(self):
        assert similarity("Daft Punk", "daft punk") == 1.0

    def test_partial(self):
        assert 0.5 < similarity("One More Time", "One More Time (Remix)") < 1.0

    def test_unrelated(self):
        assert similarity("Metallica", "Vivaldi") < 0.5

    def test_empty(self):
        assert similarity("", "Something") == 0.0

    def test_duration_score(self):
        assert duration_score(200, 201) == 1.0
        assert duration_score(200, 206) == 0.9
        assert duration_score(200, 400) < 0.5
        assert duration_score(0, 200) == 0.5

    @pytest.mark.parametrize("value,seconds", [
        (None, 0), (95, 95), ("95", 95), ("1:35", 95), ("01:35", 95),
        ("1:02:03", 3723), ("not a duration", 0), ("", 0),
    ])
    def test_parse_duration(self, value, seconds):
        assert parse_duration(value) == seconds


class TestScoreCandidate:
    def _candidate(self, **kwargs) -> TrackCandidate:
        base = dict(provider="jamendo", external_id="1",
                    title="One More Time", artist="Daft Punk", duration=320,
                    permission=PermissionStatus.DOWNLOAD_ALLOWED)
        base.update(kwargs)
        return TrackCandidate(**base)

    def test_exact_match_scores_high(self):
        q = parse("Daft Punk - One More Time")
        assert score_candidate(q, self._candidate()) >= 0.95

    def test_wrong_artist_scores_lower(self):
        q = parse("Daft Punk - One More Time")
        right = score_candidate(q, self._candidate())
        wrong = score_candidate(q, self._candidate(artist="Vivaldi"))
        assert wrong < right
        assert wrong < 0.85

    def test_version_match_adds(self):
        q = parse("Daft Punk - One More Time (Remix)")
        original = score_candidate(q, self._candidate())
        remix = score_candidate(q, self._candidate(version=TrackVersion.REMIX))
        assert remix > original

    def test_url_query_always_one(self):
        q = parse("https://open.spotify.com/track/abc")
        assert score_candidate(q, self._candidate(title="Anything")) == 1.0

    def test_result_clamped(self):
        q = parse("Daft Punk - One More Time")
        score = score_candidate(q, self._candidate(year=1998, duration=320))
        assert 0.0 <= score <= 1.0


class TestIdentity:
    def test_identity_bucket_rounds_duration(self):
        a = TrackCandidate(provider="a", external_id="1", title="Song",
                           artist="Artist", duration=300)
        b = TrackCandidate(provider="b", external_id="2", title="Song",
                           artist="Artist", duration=302)
        assert a.identity_key == b.identity_key

    def test_version_changes_identity(self):
        a = TrackCandidate(provider="a", external_id="1", title="Song", duration=300)
        b = TrackCandidate(provider="a", external_id="2", title="Song", duration=300,
                           version=TrackVersion.LIVE)
        assert a.identity_key != b.identity_key
