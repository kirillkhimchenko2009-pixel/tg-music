"""Идентификация композиций и файлов: ISRC, название, исполнитель, длительность."""
from __future__ import annotations

from pathlib import Path

from ..domain import TrackCandidate
from ..storage import sha256_file
from .normalizer import clean_title, normalize_identity, similarity


def is_same_recording(a: TrackCandidate, b: TrackCandidate,
                      tolerance: int = 5) -> bool:
    if a is b:
        return True
    if a.isrc and b.isrc and a.isrc.upper() == b.isrc.upper():
        return True
    if normalize_identity(clean_title(a.title)) != normalize_identity(clean_title(b.title)):
        return False
    if similarity(a.artist, b.artist) < 0.72:
        return False
    if a.version is not b.version:
        return False
    if a.duration and b.duration and abs(a.duration - b.duration) > tolerance:
        return False
    return True


def dedupe_exact(items: list[TrackCandidate]) -> list[TrackCandidate]:
    seen: dict[tuple[str, str], TrackCandidate] = {}
    for item in items:
        key = item.dedupe_key
        if key in seen:
            seen[key].merge_missing(item)
            if item.score > seen[key].score:
                seen[key].score = item.score
        else:
            seen[key] = item
    return list(seen.values())


def group_recordings(items: list[TrackCandidate]) -> list[list[TrackCandidate]]:
    groups: list[list[TrackCandidate]] = []
    for item in items:
        for group in groups:
            if is_same_recording(group[0], item):
                group.append(item)
                break
        else:
            groups.append([item])
    return groups


def fingerprint(path: Path) -> str:
    return sha256_file(path)


def file_cache_key(sha256: str) -> str:
    return (sha256 or "")[:32]


def same_file(path_a: Path, path_b: Path) -> bool:
    try:
        if Path(path_a).stat().st_size != Path(path_b).stat().st_size:
            return False
        return fingerprint(path_a) == fingerprint(path_b)
    except OSError:
        return False


__all__ = ["is_same_recording", "dedupe_exact", "group_recordings",
           "fingerprint", "file_cache_key", "same_file"]
