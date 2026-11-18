"""Источники кандидатов (MODULE 3, §5.1) — единый интерфейс Candidate.

SOURCE A content    — похожие по музыкальным характеристикам (жанры/настроения
                      по словам каталога: реальных аудио-тегов в БД нет —
                      параметры не выдумываем);
SOURCE B artist     — артисты LEVEL A + артисты формулы;
SOURCE C cf         — коллаборативная фильтрация лёгкая: со-лайки других
                      пользователей (k-анонимность, агрегировано);
SOURCE D session    — продолжение текущего прослушивания (SESSION TASTE);
SOURCE E context    — под активный сценарий/время;
SOURCE F discovery  — неизвестные юзеру артисты (не случайно: рядом с вкусом);
SOURCE G editorial  — пул редакции из WaveKV (если заведён), иначе молчит;
SOURCE H library    — коллекция юзера, если вписывается в сценарий;
SOURCE I popular    — популярное каталога (v3): треки без текстовых сигналов
                      (жанр/артист не распознаны) больше не выпадают из
                      ranked — иначе волна вырождается в лайки.

Каждый источник изолирован: ошибка одного не валит выдачу (endless ловит).
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Playlist, PlaylistTrack, Track, WaveKV
from .context import Context
from .taste import SessionTaste, TasteSnapshot, detect_genres, detect_moods, is_ru

LOG = logging.getLogger("wave.intel.sources")


@dataclass
class Candidate:
    track: Track
    source: str                     # A..H коды выше
    base: float = 0.0               # сила в своём источнике (0..1)
    reason: str = ""                # почему попал (для why-строки и объяснений)


@dataclass
class SourceContext:
    s: AsyncSession
    user_id: int
    ws: object                       # WaveSettings
    sess: object                     # WaveSession
    ctx: Context                     # intel.context.Context
    snap: TasteSnapshot
    st: SessionTaste
    liked: set[int] = field(default_factory=set)
    pool: dict[int, Track] = field(default_factory=dict)   # общий пул каталога
    played: set[int] = field(default_factory=set)          # отыгранные в сессии


def _top_keys(scores: dict[str, float], n: int = 4) -> list[str]:
    return [k for k, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:n]]


def _word_hit(text: str, keys: set[str], wordmap: dict) -> bool:
    low = text.lower()
    return any(any(w in low for w in wordmap.get(k, (k,))) for k in keys)


# ------------------------------ A: content ------------------------------

def src_content(sc: SourceContext) -> list[Candidate]:
    from services.wave.own_engine import GENRE_WORDS, MOOD_WORDS
    prof_genres = _top_keys(sc.snap.genre_scores, 4)
    prof_moods = _top_keys(sc.snap.mood_scores, 3)
    scen = getattr(sc.ctx, "scenario", None)
    scen_genres = set(scen.genres) if scen else set()
    scen_moods = set(scen.moods) if scen else set()
    ses_genres = _top_keys(sc.st.genres, 2)
    keys_g = set(prof_genres) | scen_genres | set(ses_genres)
    keys_m = set(prof_moods) | scen_moods
    if not (keys_g or keys_m):
        return []
    out: list[Candidate] = []
    for t in sc.pool.values():
        text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
        g_hits = {k for k in keys_g if any(w in text for w in GENRE_WORDS.get(k, (k,)))}
        m_hits = {k for k in keys_m if any(w in text for w in MOOD_WORDS.get(k, (k,)))}
        strength = 0.55 * min(1.0, len(g_hits) / 2) + 0.45 * min(1.0, len(m_hits) / 2)
        if strength <= 0:
            continue
        why = "похоже по звучанию" if g_hits and m_hits else \
            "жанр рядом с твоим вкусом" if g_hits else "настроение совпало"
        out.append(Candidate(t, "content", strength, why))
    return out


# ------------------------------ B: artist ------------------------------

def src_artist(sc: SourceContext) -> list[Candidate]:
    from .events import artist_key
    strong = set(sc.snap.levels.strong_artists)
    experimental = set(sc.snap.levels.experimental_artists)
    formula = set()
    for a in (getattr(sc.ws, "artists", None) or []):
        name = a.get("name") if isinstance(a, dict) else a
        ak = artist_key(name or "")
        if ak:
            formula.add(ak)
    ses = set(_top_keys(sc.st.artists, 3))
    weights = [(strong, 1.0, "ты часто слушаешь"),
               (ses, 0.85, "продолжим то, что сейчас играет"),
               (formula, 0.9, "артист из твоей формулы"),
               (experimental, 0.55, "артист, который ты изучаешь")]
    by_ak: dict[str, tuple[float, str]] = {}
    for group, w, why in weights:
        for ak in group:
            if ak not in by_ak:
                by_ak[ak] = (w, why)
    if not by_ak:
        return []
    out: list[Candidate] = []
    for t in sc.pool.values():
        ak = artist_key(t.artist or "")
        if ak in by_ak:
            w, why = by_ak[ak]
            out.append(Candidate(t, "artist", w, f"{why} {(t.artist or '').split(',')[0].strip()}"))
    return out


# ------------------------------ C: cf (со-лайки) ------------------------------

async def src_cf(sc: SourceContext) -> list[Candidate]:
    """Кто-то с похожими лайками слушает и это. Агрегировано, без профилей."""
    cfg_limits_min = 2
    seeds = list(sc.liked)[:20]
    if not seeds or len(sc.liked) < 3:
        return []
    # 1) владельцы, у которых в «❤» есть наши сиды
    q = (select(Playlist.owner_id)
         .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
         .where(Playlist.title.like("❤%"),
                PlaylistTrack.track_id.in_(seeds),
                Playlist.owner_id != sc.user_id)
         .distinct().limit(40))
    owners = [int(x) for x in (await sc.s.execute(q)).scalars()]
    if len(owners) < cfg_limits_min:
        return []
    # 2) сколько разных таких владельцев держит каждый трек
    q = (select(PlaylistTrack.track_id, Playlist.owner_id)
         .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
         .where(Playlist.title.like("❤%"), Playlist.owner_id.in_(owners)))
    owners_by_track: dict[int, set[int]] = {}
    for tid, owner in (await sc.s.execute(q)).all():
        owners_by_track.setdefault(int(tid), set()).add(int(owner))
    out: list[Candidate] = []
    mypool = sc.pool
    for tid, owners_set in owners_by_track.items():
        n = len(owners_set)
        if n < cfg_limits_min or tid in sc.liked or tid in sc.played:
            continue
        t = mypool.get(tid)
        if t is None:
            t = (await sc.s.execute(select(Track).where(Track.id == tid))).scalar_one_or_none()
        if t is None:
            continue
        strength = min(1.0, n / 5.0)
        out.append(Candidate(t, "cf", strength, "нравится слушателям с похожим вкусом"))
    return out


# ------------------------------ D: session ------------------------------

def src_session(sc: SourceContext) -> list[Candidate]:
    ses_artists = set(_top_keys(sc.st.artists, 4))
    ses_genres = set(_top_keys(sc.st.genres, 3))
    ses_moods = set(_top_keys(sc.st.moods, 2))
    if not (ses_artists or ses_genres or ses_moods):
        return []
    from .events import artist_key
    out: list[Candidate] = []
    for t in sc.pool.values():
        ak = artist_key(t.artist or "")
        w = 0.0
        why = ""
        if ak in ses_artists:
            w += 0.7
            why = "в духе текущей сессии"
        text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
        if ses_genres:
            from services.wave.own_engine import GENRE_WORDS
            hits = {k for k in ses_genres if any(x in text for x in GENRE_WORDS.get(k, (k,)))}
            if hits:
                w += 0.4
                why = why or "продолжает линию сессии"
        if ses_moods:
            from services.wave.own_engine import MOOD_WORDS
            hits = {k for k in ses_moods if any(x in text for x in MOOD_WORDS.get(k, (k,)))}
            if hits:
                w += 0.3
                why = why or "под настроение сессии"
        if w > 0:
            out.append(Candidate(t, "session", min(1.0, w), why))
    return out


# ------------------------------ E: context ------------------------------

def src_context(sc: SourceContext) -> list[Candidate]:
    scen = getattr(sc.ctx, "scenario", None)
    keys_g = set(scen.genres) if scen else set()
    keys_m = set(scen.moods) if scen else set()
    acts = set(scen.activities) if scen else set()
    if not (keys_g or keys_m or acts):
        return []
    from services.wave.own_engine import (ACTIVITY_WORDS, GENRE_WORDS, MOOD_WORDS)
    out: list[Candidate] = []
    for t in sc.pool.values():
        text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
        w = 0.0
        why = ""
        if keys_g and _word_hit(text, keys_g, GENRE_WORDS):
            w += 0.5
            why = "в жанр сценария"
        if keys_m and _word_hit(text, keys_m, MOOD_WORDS):
            w += 0.5
            why = why or "в настроение сценария"
        if acts and _word_hit(text, acts, ACTIVITY_WORDS):
            w += 0.4
            why = why or "под занятие"
        if w > 0:
            out.append(Candidate(t, "context", min(1.0, w), why))
    return out


# ------------------------------ F: discovery ------------------------------

def src_discovery(sc: SourceContext) -> list[Candidate]:
    """Неизвестные артисты, но не случайные: рядом по жанру/настроению с вкусом."""
    from .events import artist_key
    known = set(sc.snap.artist_scores)
    known |= set(sc.st.artists)
    known |= {artist_key(a.get("name") if isinstance(a, dict) else a)
              for a in (getattr(sc.ws, "artists", None) or [])}
    known |= {artist_key(t.artist or "")          # лайкнутые — не открытия
              for t in sc.pool.values() if t.id in sc.liked}
    anchor_genres = set(_top_keys(sc.snap.genre_scores, 3)) | \
        set(getattr(sc.ctx.scenario, "genres", frozenset()) if sc.ctx.scenario else set())
    anchor_moods = set(_top_keys(sc.snap.mood_scores, 2)) | \
        set(getattr(sc.ctx.scenario, "moods", frozenset()) if sc.ctx.scenario else set())
    from services.wave.own_engine import GENRE_WORDS, MOOD_WORDS
    max_dl = max((t.downloads or 0) for t in sc.pool.values()) or 1
    out: list[Candidate] = []
    for t in sc.pool.values():
        ak = artist_key(t.artist or "")
        if not ak or ak in known:
            continue
        text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
        near = 0.0
        if anchor_genres and any(any(w in text for w in GENRE_WORDS.get(g, (g,)))
                                 for g in anchor_genres):
            near += 0.6
        if anchor_moods and any(any(w in text for w in MOOD_WORDS.get(m, (m,)))
                                for m in anchor_moods):
            near += 0.4
        if near <= 0:
            continue
        pop = min(1.0, (t.downloads or 0) / max_dl) * 0.3
        strength = min(1.0, near * 0.7 + pop)
        out.append(Candidate(t, "discovery", strength, "новый артист рядом с твоим вкусом"))
    random.shuffle(out)     # среди равных — встряска порядка, не выборка
    return out


# ------------------------------ G: editorial ------------------------------

async def src_editorial(sc: SourceContext) -> list[Candidate]:
    """Пул редакции из WaveKV['editorial'] = [track_id...] | {'playlist_id': n}."""
    from sqlalchemy import select as _sel
    row = (await sc.s.execute(_sel(WaveKV).where(WaveKV.key == "editorial")
                              )).scalar_one_or_none()
    if not row or not isinstance(row.value, dict):
        return []
    ids: list[int] = [int(x) for x in row.value.get("track_ids", []) if str(x).lstrip("-").isdigit()]
    pid = row.value.get("playlist_id")
    if pid:
        q = (select(PlaylistTrack.track_id)
             .where(PlaylistTrack.playlist_id == int(pid))
             .limit(60))
        ids += [int(x) for x in (await sc.s.execute(q)).scalars()]
    out: list[Candidate] = []
    for tid in ids[:40]:
        t = sc.pool.get(tid) or (await sc.s.execute(
            select(Track).where(Track.id == tid))).scalar_one_or_none()
        if t is None or t.id in sc.played:
            continue
        out.append(Candidate(t, "editorial", 0.6, "в подборке редакции"))
    return out


# ------------------------------ H: library ------------------------------

def src_library(sc: SourceContext) -> list[Candidate]:
    if not sc.liked:
        return []
    scen = getattr(sc.ctx, "scenario", None)
    keys_g = set(scen.genres) if scen else set()
    keys_m = set(scen.moods) if scen else set()
    # v3: сила любимого зависит от «Разнообразия» — иначе лайки всегда наверху
    div = (getattr(sc.ws, "diversity", None) or "").strip()
    base_strength = {"favorite": 0.9, "popular": 0.55, "discover": 0.35}.get(div, 0.65)
    from services.wave.own_engine import GENRE_WORDS, MOOD_WORDS
    out: list[Candidate] = []
    for t in sc.pool.values():
        if t.id not in sc.liked or t.id in sc.played:
            continue
        strength = base_strength
        why = "твоё любимое"
        if keys_g or keys_m:
            text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
            fit = (_word_hit(text, keys_g, GENRE_WORDS) if keys_g else False) or \
                  (_word_hit(text, keys_m, MOOD_WORDS) if keys_m else False)
            if fit:
                strength = min(1.0, base_strength + 0.2)
                why = "твоё любимое — в точку сценария"
            else:
                strength = max(0.25, base_strength - 0.15)
                why = "из коллекции"
        out.append(Candidate(t, "library", strength, why))
    return out


# ------------------------------ I: popular ------------------------------

def src_popular(sc: SourceContext) -> list[Candidate]:
    """Популярное каталога: страховочный источник (v3). Каждый трек пула
    с downloads > 0 получает слабое свидетельство — иначе треки без
    распознанных жанров/артистов не попадают в ranked вовсе и волна
    вырождается в одни лайки."""
    max_dl = max((t.downloads or 0) for t in sc.pool.values()) or 1
    out: list[Candidate] = []
    for t in sc.pool.values():
        dl = t.downloads or 0
        if dl <= 0 or t.id in sc.played:
            continue
        base = 0.35 + 0.35 * min(1.0, dl / max_dl)
        out.append(Candidate(t, "popular", base, "популярное в каталоге"))
    return out


# ------------------------------ диспетчер ------------------------------

SOURCES = {
    "content": src_content,
    "artist": src_artist,
    "cf": src_cf,
    "session": src_session,
    "context": src_context,
    "discovery": src_discovery,
    "editorial": src_editorial,
    "library": src_library,
    "popular": src_popular,
}


async def collect_candidates(sc: SourceContext) -> list[Candidate]:
    """Все источники; отказ одного не мешает остальным (§19)."""
    all_c: list[Candidate] = []
    for name, fn in SOURCES.items():
        try:
            got = await fn(sc) if _is_coro_fn(fn) else fn(sc)
            all_c.extend(got or [])
        except Exception:
            LOG.exception("источник %s недоступен", name)
    return all_c


def _is_coro_fn(fn) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)
