"""Metadata Engine на Mutagen: аккуратная запись тегов и встроенной обложки.

Правила:
- не перекодируем аудио;
- не затираем корректные существующие значения;
- не придумываем отсутствующие поля;
- обложку встраиваем байт-в-байт (без resize/апскейла).
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

from ..domain import CoverAnalysis

log = logging.getLogger("music.processing.metadata")

_JUNK_RE = re.compile(r"(official\s+(video|audio)|lyrics?|visuali[sz]er|"
                      r"\.(mp3|flac|wav|m4a|ogg|opus)\s*$)", re.I)


@dataclass(slots=True)
class MetadataFields:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track_number: int | None = None
    track_total: int | None = None
    disc_number: int | None = None
    disc_total: int | None = None
    year: int | None = None
    genre: str = ""
    composer: str = ""
    isrc: str = ""

    @classmethod
    def from_candidate(cls, candidate, tags: dict | None = None) -> "MetadataFields":
        tags = tags or {}
        year = candidate.year
        if not year:
            m = re.search(r"(19|20)\d{2}", str(tags.get("date") or tags.get("year") or ""))
            year = int(m.group(0)) if m else None
        return cls(
            title=(candidate.title or "").strip(),
            artist=(candidate.artist or "").strip(),
            album=(candidate.album or "").strip(),
            album_artist=(candidate.album_artist or candidate.artist or "").strip(),
            year=year,
            genre=str(tags.get("genre") or "").strip(),
            composer=str(tags.get("composer") or "").strip(),
            isrc=(candidate.isrc or tags.get("isrc") or "").strip(),
        )

    def as_dict(self) -> dict:
        return {item.name: getattr(self, item.name) for item in fields(self)
                if getattr(self, item.name) not in ("", None)}


@dataclass(slots=True)
class MetadataEditResult:
    path: str
    changed: dict[str, str] = field(default_factory=dict)
    cover_embedded: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def detect_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _looks_junk(value: str) -> bool:
    return bool(value and _JUNK_RE.search(value))


def _needs_write(existing: str | None, new: str, overwrite: bool) -> bool:
    if not new:
        return False
    if overwrite or not existing:
        return True
    return _looks_junk(str(existing)) and not _looks_junk(new)


def write_metadata(path: str | Path, fields: MetadataFields, *,
                   cover: CoverAnalysis | bytes | None = None,
                   cover_mime: str = "", replace_cover: bool = True,
                   overwrite: bool = False) -> MetadataEditResult:
    p = Path(path)
    result = MetadataEditResult(path=str(p))
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(p)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"Mutagen не открыл файл: {exc}")
        return result
    if audio is None:
        result.errors.append("Формат не поддерживается Mutagen")
        return result

    data = _cover_bytes(cover)
    mime = cover_mime or (detect_image_mime(data) if data else "")
    try:
        writer = _writer_for(audio)
        if writer is None:
            result.errors.append(f"Нет редактора тегов для {type(audio).__name__}")
            return result
        writer(audio, fields, data, mime, replace_cover, overwrite, result)
        audio.save()
    except Exception as exc:  # noqa: BLE001
        log.warning("metadata save %s: %s", p, exc)
        result.errors.append(f"Не удалось сохранить теги: {exc}")
    return result


def _writer_for(audio):
    """Возвращает редактор строго по типу контейнера Mutagen.

    Раньше цепочка `if _write_mp3(...)` вызывала MP3-редактор для любого
    файла: у MP4/FLAC/OGG нет `delall`, и теги молча не записывались.
    """
    try:
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        from mutagen.mp4 import MP4
        from mutagen.oggopus import OggOpus
        from mutagen.oggvorbis import OggVorbis
        from mutagen.wave import WAVE
    except Exception:  # noqa: BLE001
        return None

    if isinstance(audio, MP3):
        return _write_mp3
    if isinstance(audio, MP4):
        return _write_mp4
    if isinstance(audio, FLAC):
        return _write_flac
    if isinstance(audio, (OggVorbis, OggOpus)):
        return _write_ogg
    if isinstance(audio, WAVE):
        return _write_wave
    return None


def _cover_bytes(cover) -> bytes:
    if isinstance(cover, CoverAnalysis):
        return cover.candidate.data if cover.candidate.is_valid else b""
    return cover or b""


def _existing(tags, key: str) -> str:
    try:
        value = tags.get(key)
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value) if value is not None else ""


def _write_mp3(audio, fields, data, mime, replace_cover, overwrite, result) -> bool:
    from mutagen.id3 import (APIC, TALB, TCOM, TCON, TDRC, TIT2, TPE1, TPE2,
                             TPOS, TRCK, TSRC)
    if not hasattr(audio, "tags") or audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    mapping = [
        ("title", TIT2, fields.title), ("artist", TPE1, fields.artist),
        ("album", TALB, fields.album), ("album_artist", TPE2, fields.album_artist),
        ("genre", TCON, fields.genre), ("composer", TCOM, fields.composer),
        ("isrc", TSRC, fields.isrc),
    ]
    for label, frame, value in mapping:
        if _needs_write(_existing(tags, frame.__name__), value, overwrite):
            tags.delall(frame.__name__)
            tags.add(frame(encoding=3, text=value))
            result.changed[label] = value
    if fields.year and _needs_write(_existing(tags, "TDRC"), str(fields.year), overwrite):
        tags.delall("TDRC")
        tags.add(TDRC(encoding=3, text=str(fields.year)))
        result.changed["year"] = str(fields.year)
    track = _num(fields.track_number, fields.track_total)
    if track and _needs_write(_existing(tags, "TRCK"), track, overwrite):
        tags.delall("TRCK")
        tags.add(TRCK(encoding=3, text=track))
        result.changed["track"] = track
    disc = _num(fields.disc_number, fields.disc_total)
    if disc and _needs_write(_existing(tags, "TPOS"), disc, overwrite):
        tags.delall("TPOS")
        tags.add(TPOS(encoding=3, text=disc))
        result.changed["disc"] = disc
    if data and replace_cover:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime or "image/jpeg", type=3,
                      desc="Cover", data=data))
        result.cover_embedded = True
    return True


def _write_mp4(audio, fields, data, mime, replace_cover, overwrite, result) -> bool:
    from mutagen.mp4 import MP4Cover
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    mapping = [
        ("title", "\xa9nam", fields.title), ("artist", "\xa9ART", fields.artist),
        ("album", "\xa9alb", fields.album), ("album_artist", "aART", fields.album_artist),
        ("genre", "\xa9gen", fields.genre), ("composer", "\xa9wrt", fields.composer),
    ]
    for label, key, value in mapping:
        if _needs_write(_existing(tags, key), value, overwrite):
            tags[key] = [value]
            result.changed[label] = value
    if fields.year and _needs_write(_existing(tags, "\xa9day"), str(fields.year), overwrite):
        tags["\xa9day"] = [str(fields.year)]
        result.changed["year"] = str(fields.year)
    if fields.isrc and _needs_write(_existing(tags, "----:com.apple.iTunes:ISRC"),
                                    fields.isrc, overwrite):
        try:
            tags["----:com.apple.iTunes:ISRC"] = [fields.isrc.encode()]
            result.changed["isrc"] = fields.isrc
        except Exception:  # noqa: BLE001
            pass
    if fields.track_number:
        tags["trkn"] = [(int(fields.track_number), int(fields.track_total or 0))]
        result.changed["track"] = str(fields.track_number)
    if fields.disc_number:
        tags["disk"] = [(int(fields.disc_number), int(fields.disc_total or 0))]
        result.changed["disc"] = str(fields.disc_number)
    if data and replace_cover:
        fmt = (MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG)
        tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        result.cover_embedded = True
    return True


def _write_flac(audio, fields, data, mime, replace_cover, overwrite, result) -> bool:
    from mutagen.flac import Picture
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    mapping = [
        ("title", "title", fields.title), ("artist", "artist", fields.artist),
        ("album", "album", fields.album),
        ("album_artist", "albumartist", fields.album_artist),
        ("genre", "genre", fields.genre), ("composer", "composer", fields.composer),
        ("isrc", "isrc", fields.isrc),
    ]
    for label, key, value in mapping:
        if _needs_write(_existing(tags, key), value, overwrite):
            tags[key] = [value]
            result.changed[label] = value
    if fields.year and _needs_write(_existing(tags, "date"), str(fields.year), overwrite):
        tags["date"] = [str(fields.year)]
        result.changed["year"] = str(fields.year)
    if fields.track_number:
        tags["tracknumber"] = [str(fields.track_number)]
        result.changed["track"] = str(fields.track_number)
    if fields.disc_number:
        tags["discnumber"] = [str(fields.disc_number)]
        result.changed["disc"] = str(fields.disc_number)
    if data and (replace_cover or not getattr(audio, "pictures", None)):
        audio.clear_pictures()
        picture = Picture()
        picture.type = 3
        picture.mime = mime or "image/jpeg"
        picture.desc = "Cover"
        picture.data = data
        audio.add_picture(picture)
        result.cover_embedded = True
    return True


def _write_ogg(audio, fields, data, mime, replace_cover, overwrite, result) -> bool:
    from mutagen.flac import Picture
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    mapping = [
        ("title", "title", fields.title), ("artist", "artist", fields.artist),
        ("album", "album", fields.album),
        ("album_artist", "albumartist", fields.album_artist),
        ("genre", "genre", fields.genre), ("composer", "composer", fields.composer),
        ("isrc", "isrc", fields.isrc),
    ]
    for label, key, value in mapping:
        if _needs_write(_existing(tags, key), value, overwrite):
            tags[key] = [value]
            result.changed[label] = value
    if fields.year and _needs_write(_existing(tags, "date"), str(fields.year), overwrite):
        tags["date"] = [str(fields.year)]
        result.changed["year"] = str(fields.year)
    if fields.track_number:
        tags["tracknumber"] = [str(fields.track_number)]
    if fields.disc_number:
        tags["discnumber"] = [str(fields.disc_number)]
    if data and replace_cover:
        picture = Picture()
        picture.type = 3
        picture.mime = mime or "image/jpeg"
        picture.desc = "Cover"
        picture.data = data
        tags["metadata_block_picture"] = [
            base64.b64encode(picture.write()).decode("ascii")]
        result.cover_embedded = True
    return True


def _write_wave(audio, fields, data, mime, replace_cover, overwrite, result) -> bool:
    try:
        from mutagen.id3 import APIC, TALB, TCOM, TCON, TDRC, TIT2, TPE1, TPE2, TSRC
    except Exception:  # noqa: BLE001
        return False
    if audio.tags is None:
        try:
            audio.add_tags()
        except Exception:  # noqa: BLE001
            return False
    tags = audio.tags
    mapping = [
        ("title", TIT2, fields.title), ("artist", TPE1, fields.artist),
        ("album", TALB, fields.album), ("album_artist", TPE2, fields.album_artist),
        ("genre", TCON, fields.genre), ("composer", TCOM, fields.composer),
        ("isrc", TSRC, fields.isrc),
    ]
    for label, frame, value in mapping:
        if _needs_write(_existing(tags, frame.__name__), value, overwrite):
            tags.delall(frame.__name__)
            tags.add(frame(encoding=3, text=value))
            result.changed[label] = value
    if fields.year:
        tags.delall("TDRC")
        tags.add(TDRC(encoding=3, text=str(fields.year)))
        result.changed["year"] = str(fields.year)
    if data and replace_cover:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime or "image/jpeg", type=3,
                      desc="Cover", data=data))
        result.cover_embedded = True
    return True


def _num(number: int | None, total: int | None) -> str:
    if not number:
        return ""
    return f"{int(number)}/{int(total)}" if total else str(int(number))


def read_metadata(path: str | Path) -> dict[str, str]:
    from ..quality.metadata_checker import normalize_tags
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        if not audio:
            return {}
        tags = getattr(audio, "tags", None)
        return normalize_tags(dict(tags) if tags else {})
    except Exception:  # noqa: BLE001
        return {}


def embedded_cover(path: str | Path) -> tuple[bytes, str] | None:
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        tags = getattr(audio, "tags", None)
        if tags is None:
            return None
        if hasattr(audio, "pictures") and audio.pictures:
            pic = audio.pictures[0]
            return pic.data, pic.mime or detect_image_mime(pic.data)
        if "covr" in tags and tags["covr"]:
            covr = tags["covr"][0]
            data = bytes(covr)
            fmt = getattr(covr, "imageformat", None)
            mime = "image/png" if fmt == 14 else "image/jpeg"
            return data, mime
        for key in tags.keys():
            if str(key).startswith("APIC"):
                frame = tags[key]
                return frame.data, frame.mime or detect_image_mime(frame.data)
        raw = tags.get("metadata_block_picture")
        if raw:
            from mutagen.flac import Picture
            data = raw[0] if isinstance(raw, list) else raw
            pic = Picture(base64.b64decode(data))
            return pic.data, pic.mime or detect_image_mime(pic.data)
    except Exception:  # noqa: BLE001
        return None
    return None


__all__ = ["MetadataFields", "MetadataEditResult", "write_metadata",
           "read_metadata", "embedded_cover", "detect_image_mime"]
