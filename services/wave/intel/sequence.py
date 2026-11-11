"""Wave Sequence Optimizer (§6.2): качество не только треков, но и переходов.

Плохая последовательность: спокойный джаз → агрессивный металл → детская песня.
Хорошая: спокойный джаз → мягкий соул → атмосферная электроника.

Оптимизатор жадный: из лучших по score берём тот, что даёт плавный переход от
хвоста потока (настроение/жанр/язык). Жёсткие правила: один артист не звучит
дважды в партии (кроме крошечного каталога), соседние треки не прыгают между
противоположными настроениями.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.models import Track
from .config import WaveConfig
from .events import artist_key
from .ranker import ScoredCandidate
from .taste import detect_genres, detect_moods, is_ru


def _mood_distance(a: set[str], b: set[str]) -> float:
    """0 — то же настроение; 1 — противоположные полюса."""
    if not a or not b:
        return 0.2
    calm = {"calm", "sad"}
    drive = {"active", "fun", "edge"}
    if (a & calm) and (b & drive) or (a & drive) and (b & calm):
        return 1.0
    if a & b:
        return 0.0
    return 0.4


def _transition_cost(prev: Track, nxt: Track) -> float:
    cost = 0.0
    cost += _mood_distance(detect_moods(prev), detect_moods(nxt))
    ga, gb = detect_genres(prev), detect_genres(nxt)
    if ga and gb and not (ga & gb):
        cost += 0.35                    # смена жанровой семьи
    if ga and not gb or gb and not ga:
        cost += 0.05
    if is_ru(prev) != is_ru(nxt):
        cost += 0.15                    # языковой прыжок — заметен
    return min(1.0, cost)


@dataclass
class SequenceResult:
    picks: list[ScoredCandidate]
    bridge_used: bool = False           # был ли жанровый мост в переходе


def optimize_sequence(ranked: list[ScoredCandidate], n: int,
                      tail: list[Track], cfg: WaveConfig) -> SequenceResult:
    """tail — последние треки потока (сессии); возвращаем упорядоченную партию."""
    if not ranked:
        return SequenceResult([])
    weights = cfg.weights
    lam = 0.45                          # сила штрафов за резкие переходы
    picked: list[ScoredCandidate] = []
    used_artists: set[str] = set()
    anchor: Track | None = tail[-1] if tail else None

    pool = list(ranked)
    while pool and len(picked) < n:
        best_i = 0
        best_v = -10 ** 9
        bridge = False
        for i, cand in enumerate(pool):
            t = cand.track
            ak = artist_key(t.artist or "")
            if ak and ak in used_artists:
                continue                # артист не дважды в партии
            cost = _transition_cost(anchor, t) if anchor is not None else 0.0
            val = cand.score - lam * cost
            if val > best_v:
                best_v, best_i, bridge = val, i, cost > 0.5
        if best_v <= -10 ** 8:          # весь пул — один артист: снимаем запрет
            best_i = 0
        cand = pool.pop(best_i)
        picked.append(cand)
        used_artists.add(artist_key(cand.track.artist or ""))
        anchor = cand.track

    return SequenceResult(picked, bridge_used=any(
        _transition_cost(picked[i - 1].track, picked[i].track) <= 0.35
        for i in range(1, len(picked))))


def build_tail(sess, limit: int = 4) -> list[Track]:
    """Хвост потока из сессии: последние отыгранные Track-объекты (в памяти)."""
    tracks = getattr(sess, "_tail_tracks", None) or []
    return list(tracks)[-limit:]
