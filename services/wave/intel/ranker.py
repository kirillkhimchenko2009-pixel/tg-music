"""Ranking Engine (MODULE 3, §5.3): единый Recommendation Score.

Score = TasteFit + ContextFit + SessionFit + Discovery + AudioQuality
      + Freshness + Language + MoodAudio (веса из intel/config, сумма 1.00)

Все факторы нормализованы в 0..1. Веса — из intel/config (WaveKV), не из кода.
Аудио-параметры не выдумываются: AudioQuality — реальный битрейт/формат из
каталога; MoodAudio — BPM/энергия/валентность/танцевальность из
services/audio_features (анализ файла ffmpeg+numpy); сходство — по словам
жанров/настроений и артистам.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from core.models import Track, utcnow
from .config import WaveConfig, clamp01
from .context import Scenario
from .events import artist_key
from .taste import SessionTaste, TasteSnapshot, detect_genres, detect_moods, is_ru
from .sources import Candidate, SourceContext

# качество файла по метаданным каталога (без выдумывания аудио-фич)
_QUALITY_MAP = (
    ("flac", 1.00), ("alac", 1.00),
    ("aac320", 0.90), ("320", 0.88), ("mp3_320", 0.88),
    ("aac256", 0.80), ("256", 0.78),
    ("aac192", 0.68), ("192", 0.66),
    ("128", 0.50),
)


def audio_quality(t: Track) -> float:
    q = (t.quality or "").lower()
    for key, val in _QUALITY_MAP:
        if key in q:
            return val
    if t.size_bytes and t.duration:
        # ~килобит/сек как грубая оценка качества
        kbps = (t.size_bytes * 8) / max(1, t.duration) / 1000
        if kbps >= 300:
            return 0.9
        if kbps >= 200:
            return 0.75
        if kbps >= 120:
            return 0.55
        return 0.4
    return 0.45


SKIP_CLASS_PENALTY = {
    "first_seconds": 1.0,    # мгновенное «нет»
    "few_seconds": 0.8,
    "mid": 0.5,              # смена настроения — слабый сигнал
    "near_end": 0.25,        # почти дослушал — «было норм»
    "after_similar": 0.4,    # усталость от направления, не от трека
}


@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: float = 0.0
    factors: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    why: str = ""

    @property
    def track(self) -> Track:
        return self.candidate.track


def _taste_fit(t: Track, sc: SourceContext) -> float:
    snap: TasteSnapshot = sc.snap
    ak = artist_key(t.artist or "")
    parts = [snap.artist_fit(ak) * 0.55,
             snap.genre_fit(detect_genres(t)) * 0.25,
             snap.mood_fit(detect_moods(t)) * 0.20]
    if t.id in sc.liked:
        # v3: лайк — сильный сигнал (0.88), но не автоматическая победа:
        # при 1000+ лайков иначе вся волна вырождается в «Любимое»
        parts.append(0.88)
    return clamp01(max(parts) if parts else 0.0)


def _context_fit(t: Track, sc: SourceContext, scen: Scenario | None) -> float:
    if scen is None:
        return 0.35                     # без сценария — нейтрально, не наказываем
    from services.wave.own_engine import GENRE_WORDS, MOOD_WORDS, ACTIVITY_WORDS
    from .taste import detect_genres as _dg, detect_moods as _dm
    text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
    w = 0.30                            # база «волне есть куда ставить трек»
    hits_m = any(any(x in text for x in MOOD_WORDS.get(m, (m,)))
                 for m in (scen.moods or frozenset()))
    hits_g = any(any(x in text for x in GENRE_WORDS.get(g, (g,)))
                 for g in (scen.genres or frozenset()))
    hits_a = any(any(x in text for x in ACTIVITY_WORDS.get(a, (a,)))
                 for a in (scen.activities or frozenset()))
    # v3: хиты формулы весомее, детектируемый промах штрафуется — смена
    # настроения/жанра в формуле заметно перетасовывает партию
    if scen.moods:
        w += 0.55 if hits_m else 0.0
        if not hits_m and _dm(t) and not (_dm(t) & set(scen.moods)):
            w -= 0.15                   # настроение трека известно и противоречит
    if scen.genres:
        w += 0.40 if hits_g else 0.0
        if not hits_g and _dg(t) and not (_dg(t) & set(scen.genres)):
            w -= 0.15                   # жанр трека определён — мимо формулы
    if scen.activities:
        w += 0.25 if hits_a else 0.0
    # язык формулы учён отдельным фактором language (см. rank)
    return clamp01(w)


def _language_fit(t: Track, ws) -> float:
    """Формула задаёт язык (ru / non-ru): 1.0 совпал, 0.0 противоречит,
    0.5 нейтрально (any / не задан) — одинаковый вклад всем трекам."""
    lang = (getattr(ws, "language", "any") or "any")
    if lang in (None, "", "any"):
        return 0.5
    ru = is_ru(t)
    return 1.0 if ((lang == "ru") == ru) else 0.0


def _mood_audio_fit(t: Track, scen: Scenario | None) -> float:
    """Настроение по реальному звуку (BPM/энергия/валентность/танец).

    Раньше настроение ловилось только по словам в названии («грустная песня»
    в заголовке — редкость). Теперь services/audio_features считает признаки
    файла, и «бодрое / энергичное / грустное» работает по музыке, а не тексту.
    Треки без анализа — нейтральные 0.5: фактор никого не выделяет.
    """
    if scen is None or not scen.moods or t.analyzed_at is None:
        return 0.5
    bpm = float(t.bpm or 0.0)
    energy = clamp01(float(t.energy or 0.0))
    valence = clamp01(float(t.valence or 0.0))
    dance = clamp01(float(t.danceability or 0.0))
    tempo = clamp01((bpm - 60.0) / 120.0)       # 60 BPM → 0, 180 BPM → 1
    by_mood: dict[str, float] = {}
    if "fun" in scen.moods:
        by_mood["fun"] = 0.45 * energy + 0.30 * valence + 0.25 * dance
    if "active" in scen.moods:
        by_mood["active"] = 0.50 * tempo + 0.35 * energy + 0.15 * dance
    if "calm" in scen.moods:
        by_mood["calm"] = 1.0 - (0.55 * tempo + 0.45 * energy)
    if "sad" in scen.moods:
        by_mood["sad"] = (0.50 * (1.0 - valence) + 0.30 * (1.0 - tempo)
                          + 0.20 * (1.0 - energy))
    if "edge" in scen.moods:
        by_mood["edge"] = 0.45 * energy + 0.30 * (1.0 - valence) + 0.25 * tempo
    if not by_mood:
        return 0.5
    return clamp01(max(by_mood.values()))


def _session_fit(t: Track, st: SessionTaste) -> float:
    ak = artist_key(t.artist or "")
    w = 0.0
    if st.artists:
        top = max(st.artists.values()) or 1.0
        w += 0.6 * (st.artists.get(ak, 0.0) / top)
    gset = detect_genres(t)
    if st.genres and gset:
        top = max(st.genres.values()) or 1.0
        best = max((st.genres.get(g, 0.0) for g in gset), default=0.0)
        w += 0.25 * (best / top)
    mset = detect_moods(t)
    if st.moods and mset:
        top = max(st.moods.values()) or 1.0
        best = max((st.moods.get(m, 0.0) for m in mset), default=0.0)
        w += 0.15 * (best / top)
    return clamp01(w)


def _discovery_fit(t: Track, c: Candidate, sc: SourceContext) -> float:
    ak = artist_key(t.artist or "")
    known = ak in sc.snap.artist_scores or ak in sc.st.artists or t.id in sc.liked
    if known:
        return 0.15                     # знакомое — свежести почти не даёт
    return clamp01(0.5 + 0.5 * c.base) if c.source == "discovery" else 0.6


def _freshness(t: Track, sc: SourceContext, days_no_repeat: float) -> float:
    if t.id in (getattr(sc.ctx, "recently_played_ids", set()) or set()):
        return 0.15                     # играл в окне повторения — свежести почти нет
    base = 0.85
    if t.created_at:
        age_days = (utcnow() - t.created_at).total_seconds() / 86400.0
        if age_days <= 14:
            base += 0.15                # новинка каталога
    return clamp01(base)


def rank(sc: SourceContext, candidates: list[Candidate], cfg: WaveConfig) -> list[ScoredCandidate]:
    w = cfg.weights
    p = cfg.penalty
    limits = cfg.limits
    scen: Scenario | None = getattr(sc.ctx, "scenario", None)
    hard_skip = float(limits.get("hard_skip_seconds", 25))
    freeze_days = float(limits.get("skip_freeze_days", 3))
    cooldown = int(limits.get("artist_cooldown_tracks", 4))
    days_no_repeat = float(limits.get("days_no_repeat", 7))

    # скипы последних freeze_days → худший класс по треку
    skip_worst: dict[int, str] = {}
    now = utcnow()
    for ev in getattr(sc.ctx, "recent_skips", []) or []:
        cls = ev.get("class") or "few_seconds"
        tid = ev.get("track_id")
        if tid is not None:
            cur = skip_worst.get(tid)
            if cur is None or SKIP_CLASS_PENALTY.get(cls, 0.5) > SKIP_CLASS_PENALTY.get(cur, 0.5):
                skip_worst[tid] = cls

    # артист-усталость: сколько раз артист звучал в последних N треках потока
    recent_artists: dict[str, int] = {}
    for t in getattr(sc.ctx, "recent_tracks", []) or []:
        ak = artist_key(t.artist or "")
        if ak:
            recent_artists[ak] = recent_artists.get(ak, 0) + 1

    excluded_artists = set(sc.snap.excluded_artists)
    excluded_genres = set(sc.snap.excluded_genres)
    disliked_artists = sc.snap.disliked_artist_keys

    # дедуп по треку: берём лучшее свидетельство + бонус за широту источников
    by_track: dict[int, list[Candidate]] = {}
    for c in candidates:
        by_track.setdefault(c.track.id, []).append(c)

    scored: list[ScoredCandidate] = []
    for tid, group in by_track.items():
        t = group[0].track
        ak = artist_key(t.artist or "")
        if t.id in sc.played:
            continue                                    # уже отыграно в сессии
        if t.id in sc.snap.disliked_track_ids:
            continue
        if ak in disliked_artists or ak in excluded_artists:
            continue
        gset = detect_genres(t)
        if excluded_genres & gset:
            continue
        if not getattr(sc.ws, "allow_explicit", True) and t.explicit:
            continue
        if ak in recent_artists and recent_artists[ak] >= cooldown:
            continue                                    # жёсткий cooldown в партии

        best = max(group, key=lambda c: c.base)
        extra_src = len({c.source for c in group}) - 1

        factors = {
            "taste_fit": _taste_fit(t, sc),
            "context_fit": _context_fit(t, sc, scen),
            "session_fit": _session_fit(t, sc.st),
            "discovery": _discovery_fit(t, best, sc),
            "audio_quality": audio_quality(t),
            "freshness": _freshness(t, sc, days_no_repeat),
            "language": _language_fit(t, sc.ws),
            "mood_audio": _mood_audio_fit(t, scen),
        }
        penalties: dict[str, float] = {}
        # повтор: скипы (freeze) и недавние прослушивания
        if tid in skip_worst:
            cls = skip_worst[tid]
            penalties["skip_recent"] = p.get("skip_recent", 0.35) * SKIP_CLASS_PENALTY.get(cls, 0.5)
        # артист-усталость: soft, если звучал недавно, но реже cooldown
        if ak in recent_artists:
            n = recent_artists[ak]
            penalties["artist_fatigue"] = p.get("artist_fatigue", 0.55) * min(1.0, n / max(1, cooldown))
        # недавнее прослушивание (48ч окно — жёстче окна days_no_repeat)
        if t.id in (getattr(sc.ctx, "recent_48h_ids", set()) or set()):
            penalties["repeat"] = p.get("repeat", 0.6)

        score = sum(w.get(k, 0.0) * v for k, v in factors.items())
        score -= sum(penalties.values())
        score += min(0.10, 0.03 * max(0, extra_src))    # широта свидетельств

        # v3: «Разнообразие» из формулы реально меняет порядок
        div = (getattr(sc.ws, "diversity", None) or "").strip()
        if div == "popular":
            max_dl = max((c.track.downloads or 0) for c in candidates) or 1
            score += 0.18 * min(1.0, (t.downloads or 0) / max_dl)
        elif div == "discover":
            if t.id in sc.liked:
                score -= 0.20               # лайки в «Открытиях» осаживаем
            elif ak not in sc.snap.artist_scores:
                score += 0.10               # незнакомые артисты — наверх
        elif div == "favorite":
            if t.id in sc.liked:
                score += 0.12

        why = best.reason or "подобрано по твоей формуле"
        scored.append(ScoredCandidate(best, score, factors, penalties, why))

    scored.sort(key=lambda s: (s.score, s.track.downloads or 0), reverse=True)
    return scored
