"""Матчинг треков между сервисами: точный по ISRC, иначе fuzzy по тексту."""
import difflib
import re

from services.providers.base import TrackMeta

_STOP = re.compile(r"[^\wа-яё ]+", re.IGNORECASE)

def norm(s: str | None) -> str:
    s = (s or "").lower().strip()
    s = _STOP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def similarity(a: TrackMeta, b: TrackMeta) -> float:
    """0..1 — совпадение треков по тексту и (примерно) длительности."""
    na, nb = norm(f"{a.artist} {a.title}"), norm(f"{b.artist} {b.title}")
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    dur_penalty = 0.0
    if a.duration and b.duration and abs(a.duration - b.duration) > 5:
        dur_penalty = 0.15 * min(abs(a.duration - b.duration) / 60, 1.0)
    return max(0.0, ratio - dur_penalty)

def best_match(target: TrackMeta, candidates: list[TrackMeta],
               min_score: float = 0.62) -> TrackMeta | None:
    """Лучший кандидат по ISRC (если есть с обеих сторон), иначе по score."""
    for c in candidates:
        if target.isrc and c.isrc and target.isrc.upper() == c.isrc.upper():
            return c
    scored = [(similarity(target, c), c) for c in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] >= min_score else None
