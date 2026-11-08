"""Объединение результатов: дедупликация, выбор представителя, ранжирование."""
from __future__ import annotations

from ..domain import TrackCandidate
from ..enums import PermissionStatus, TrackVersion
from .duplicate_detector import dedupe_exact, group_recordings
from .normalizer import score_candidate


def quality_rank(candidate: TrackCandidate) -> tuple[int, int, int]:
    fmt = (candidate.file_format or "").lower()
    lossless = 1 if fmt in ("flac", "wav", "alac", "aiff", "ape") else 0
    bitrate = int(candidate.bitrate_kbps or 0)
    duration_ok = 1 if 20 <= int(candidate.duration or 0) <= 7200 else 0
    return lossless, bitrate, duration_ok


def _completeness(candidate: TrackCandidate) -> int:
    fields = (candidate.artist, candidate.album, candidate.cover_url,
              candidate.duration, candidate.year, candidate.source_url,
              candidate.license_name)
    return sum(1 for value in fields if value)


def _representative_key(candidate: TrackCandidate) -> tuple:
    allowed = 1 if candidate.permission is PermissionStatus.DOWNLOAD_ALLOWED else 0
    quality = quality_rank(candidate)
    return (allowed, quality, _completeness(candidate), candidate.score)


def _merge_group(group: list[TrackCandidate]) -> TrackCandidate:
    best = max(group, key=_representative_key)
    others = []
    for other in group:
        if other is best:
            continue
        best.merge_missing(other)
        if not best.cover_url and other.cover_url:
            best.cover_url = other.cover_url
        others.append(other)
    # альтернативные источники: только с другим провайдером/ссылкой
    seen = {(best.provider, best.external_id)}
    alternatives = []
    for other in others:
        key = (other.provider, other.external_id)
        if key in seen:
            continue
        seen.add(key)
        alternatives.append(other)
    best.alternatives = alternatives[:5]
    if alternatives:
        labels = []
        for alt in best.alternatives:
            label = alt.provider_label or alt.provider
            if label not in labels:
                labels.append(label)
        best.extra["also_on"] = labels
    return best


class ResultMerger:
    def merge(self, results: list[TrackCandidate], query=None,
              limit: int = 12) -> list[TrackCandidate]:
        items = dedupe_exact(results)
        if query is not None:
            for item in items:
                item.score = score_candidate(query, item)
        groups = group_recordings(items)
        merged = [_merge_group(group) for group in groups]
        # одинаковые версии из разных источников уже в alternatives;
        # разные версии остаются отдельными карточками
        merged.sort(key=lambda c: (
            1 if c.permission is PermissionStatus.DOWNLOAD_ALLOWED else 0,
            c.score,
            quality_rank(c),
            _completeness(c),
            -abs(int(c.duration or 0)),
        ), reverse=True)
        # не показываем десятки копий одной и той же версии
        per_identity: dict[str, int] = {}
        out: list[TrackCandidate] = []
        for item in merged:
            base = f"{item.identity_key.split('|')[0]}|{item.identity_key.split('|')[1]}|{item.version.value}"
            count = per_identity.get(base, 0)
            if count >= 3 and item.version is TrackVersion.ORIGINAL:
                continue
            per_identity[base] = count + 1
            out.append(item)
            if len(out) >= limit:
                break
        return out


merger = ResultMerger()

__all__ = ["ResultMerger", "merger", "quality_rank"]
