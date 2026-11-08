"""Проверка музыкальных метаданных: полнота, мусор, рассинхронизация."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..search.normalizer import clean_title, normalize_identity, similarity

_FIELDS = ("title", "artist", "album", "album_artist", "date", "genre",
           "composer", "isrc", "track", "disc")
_JUNK_RE = re.compile(r"(official\s+(?:video|audio)|lyrics?|visuali[sz]er|"
                      r"free\s+download|320\s*kbps|\.(?:mp3|flac|wav|m4a|ogg)\b)", re.I)

# MP4-атомы Mutagen: "\xa9nam" и т.п. После normalize_tags ключи в нижнем
# регистре. Без этих псевдонимов m4a-файлы выглядели «пустыми».
_MP4_ALIASES = {
    "title": ("\xa9nam",),
    "artist": ("\xa9art",),
    "album": ("\xa9alb",),
    "album_artist": ("aart",),
    "date": ("\xa9day",),
    "genre": ("\xa9gen",),
    "composer": ("\xa9wrt",),
    "isrc": ("----:com.apple.itunes:isrc",),
}


@dataclass(slots=True)
class MetadataReport:
    fields: dict[str, str] = field(default_factory=dict)
    title: str = ""
    artist: str = ""
    album: str = ""
    year: int | None = None
    isrc: str | None = None
    has_cover: bool = False
    issues: list[str] = field(default_factory=list)
    suspicious: bool = False

    @property
    def complete(self) -> bool:
        return bool(self.title and self.artist and self.album)

    def as_dict(self) -> dict:
        return {"fields": self.fields, "title": self.title,
                "artist": self.artist, "album": self.album,
                "year": self.year, "isrc": self.isrc,
                "has_cover": self.has_cover, "issues": self.issues,
                "suspicious": self.suspicious}


def _as_text(value) -> str:
    """MP4FreeForm — bytes-подкласс; tuple — номер трека/диска."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            return value.hex()
    if isinstance(value, tuple) and value:
        return "/".join(str(part) for part in value if part or part == 0)
    return str(value).strip()


def normalize_tags(tags: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (tags or {}).items():
        if value is None:
            continue
        name = str(key).strip().lower().replace(" ", "_")
        if isinstance(value, list):
            value = value[0] if value else ""
        text = _as_text(value)
        out[name] = text
        for canonical, aliases in _MP4_ALIASES.items():
            if name in aliases and canonical not in out:
                out[canonical] = text
    return out


def _first(raw: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value:
            return value
    return ""


def inspect(tags: dict | None, *, expected_title: str = "",
            expected_artist: str = "", expected_album: str = "",
            expected_duration: int = 0, actual_duration: float = 0,
            filename: str = "", has_cover: bool = False) -> MetadataReport:
    raw = normalize_tags(tags)
    report = MetadataReport(fields=raw, has_cover=has_cover)
    report.title = _first(raw, "title", "tit2", "\xa9nam")
    report.artist = _first(raw, "artist", "tpe1", "\xa9art", "albumartist",
                           "album_artist", "aart")
    report.album = _first(raw, "album", "talb", "\xa9alb")
    report.isrc = _first(raw, "isrc", "tsrc",
                         "----:com.apple.itunes:isrc") or None
    date = _first(raw, "date", "year", "tdrc", "\xa9day")
    m = re.search(r"(19|20)\d{2}", date)
    if m:
        report.year = int(m.group(0))

    if not report.title:
        report.issues.append("нет тега Title")
    if not report.artist:
        report.issues.append("нет тега Artist")
    if not report.album:
        report.issues.append("нет тега Album")
    if not has_cover:
        report.issues.append("нет встроенной обложки")

    if report.title and _JUNK_RE.search(report.title):
        report.issues.append("Title содержит мусорные пометки")
        report.suspicious = True
    if report.title and len(report.title) > 180:
        report.issues.append("Слишком длинный Title")
        report.suspicious = True
    if filename:
        stem = Path(filename).stem
        if report.title and normalize_identity(report.title) == normalize_identity(stem):
            report.issues.append("Title совпадает с именем файла")
            report.suspicious = True
    if report.artist and _JUNK_RE.search(report.artist):
        report.issues.append("Artist содержит мусорные пометки")
        report.suspicious = True

    if expected_title and report.title:
        if similarity(clean_title(expected_title), clean_title(report.title)) < 0.55:
            report.issues.append("Title не совпадает с найденной композицией")
            report.suspicious = True
    if expected_artist and report.artist:
        if similarity(expected_artist, report.artist) < 0.45:
            report.issues.append("Artist не совпадает с найденной композицией")
            report.suspicious = True
    if expected_album and report.album:
        if similarity(expected_album, report.album) < 0.45:
            report.issues.append("Album отличается от найденного")
    if expected_duration and actual_duration:
        diff = abs(float(expected_duration) - float(actual_duration))
        if diff > max(10.0, expected_duration * 0.12):
            report.issues.append(
                f"Длительность {int(actual_duration)}с расходится с ожидаемой "
                f"{int(expected_duration)}с")
            report.suspicious = True
    return report


__all__ = ["MetadataReport", "inspect", "normalize_tags"]
