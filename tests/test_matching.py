"""Релевантность для русскоязычного поиска (мастер-промпт §6): NFKD,
кавычки, транслитерация, версии (оригинал ≠ ремикс/лайв)."""
from __future__ import annotations

from services.matching import (best_match, norm, norm_xlit, similarity,
                               version_of)
from services.providers.base import TrackMeta


def _t(title: str, artist: str = "", duration: int = 0,
       isrc: str | None = None) -> TrackMeta:
    return TrackMeta(source="x", external_id="1", title=title, artist=artist,
                     duration=duration, isrc=isrc)


class TestNormalization:
    def test_quotes_collapsed(self):
        assert norm("«Кино» — «Группа крови»") == norm("кино группа крови")

    def test_nfkd_and_spaces(self):
        assert norm("Кино \u00a0 Группа ") == "кино группа"
        assert norm("Ёлка — Прованс") == "елка прованс"

    def test_transliteration(self):
        assert norm_xlit("Кино — Группа крови") == "kino gruppa krovi"

    def test_same_after_xlit(self):
        """Кириллица и латиница пишутся одинаково после транслита."""
        assert norm_xlit("Кино") == norm_xlit("Kino")
        assert norm_xlit("Группа крови") == norm_xlit("Gruppa Krovi")


class TestVersions:
    def test_version_extracted(self):
        assert "remix" in version_of("Кино — Группа крови (DJ Antonio Remix)")
        assert version_of("Кино — Группа крови") == ""          # оригинал без метки

    def test_original_not_merged_with_remix(self):
        """Оригинал, ремикс и концертная версия — разные треки (§6)."""
        original = _t("Группа крови", "Кино", duration=285)
        remix = _t("Группа крови (Dj Antonio Remix)", "Кино", duration=285)
        live = _t("Группа крови (Live)", "Кино", duration=296)
        assert similarity(original, remix) < 0.70
        assert similarity(original, live) < 0.70
        assert best_match(original, [remix, live]) is None

    def test_same_version_matches(self):
        a = _t("Группа крови (Dj Antonio Remix)", "Кино", 285)
        b = _t("Группа крови (Dj Antonio Remix)", "Кино", 286)
        assert similarity(a, b) > 0.85

    def test_feat_versions_differ(self):
        a = _t("Summer", "Artist")
        b = _t("Summer (feat. Someone)", "Artist")
        assert similarity(a, b) < 0.85


class TestTranslitMatching:
    def test_cyrillic_matches_latin(self):
        """«Кино — Группа крови» == «Kino — Gruppa Krovi» — полное совпадение."""
        a = _t("Группа крови", "Кино", 285)
        b = _t("Gruppa Krovi", "Kino", 285)
        assert similarity(a, b) > 0.90

    def test_isrc_wins_over_text(self):
        a = _t("Совсем другое название", "Кто-то", 10, isrc="RU0012300012")
        cands = [_t("Группа крови", "Кино", 285, isrc="RU0012300012")]
        assert best_match(a, cands) is cands[0]

    def test_duration_penalty_kept(self):
        a = _t("Группа крови", "Кино", 285)
        b = _t("Группа крови", "Кино", 480)
        assert similarity(a, b) < similarity(a, _t("Группа крови", "Кино", 285))
