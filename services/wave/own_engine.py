"""Свой движок волны (движок B): работает всегда — для гостей и как фолбэк A.

Ядро вкуса (всё из нашей БД):
  • лайки — плейлист «❤ Любимое» (title начинается с «❤»);
  • дослушивания — play_events finished=true (или played >= 60 c);
  • скипы — play_events skipped=true, < 25 с (жёсткий сигнал «не хочу»);
  • дисклайки — track_feedback kind=-1 (мораторий: трек всегда, артист 30 дн).

Кандидаты: популярное в каталоге + плейлисты юзера + его лайки. Скоринг
объяснимый (формула в константах ниже), у каждого трека — why-строка
(«ты часто слушаешь …», «жанр из формулы», «новое для тебя»).

Расширение: лёгкая доля «эксплор»-слотов (новое вне ядра вкуса).
"""
import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (PlayEvent, Playlist, PlaylistTrack, Track, TrackFeedback,
                         WaveSession, utcnow)

LOG = logging.getLogger("wave.own")

BATCH = 3

# --- веса скоринга (v2, объяснимая формула) ---
W_ARTIST = 0.34        # аффинити к артисту (лайки + дослушивания)
W_LIKED = 0.30         # трек прямо из «Любимого»
W_FIT = 0.18           # попадание в формулу (жанры/настроение/занятие словам)
W_POPULAR = 0.10       # глобальная популярность (downloads)
W_FORMULA_ARTIST = 0.22  # артист, вписанный в формулу волны
EXPLORE_SHARE = 0.25   # дефолт-шанс «нового» трека в партии (или по разнообразию)

PENALTY_REPLAY = 0.45  # артист уже играл в последних треках партии/сессии
SKIP_SCORE = -10.0     # недавний быстрый скип — трек заморожен на дне списка

LANG_BONUS = 0.05      # язык трека совпал с языком формулы
LANG_PENALTY = 0.18    # язык трека противоречит формуле

HARD_SKIP_SECONDS = 25          # скип быстрее N с = «не хочу»
ARTIST_COOLDOWN_TRACKS = 4      # не подсовывать артиста сразу после него
DAYS_NO_REPEAT = 7              # недавно игравший трек отдыхает N дней

# разнообразие (formula.diversity) → доля «нового» в партии и вес популярного
EXPLORE_BY_DIVERSITY = {"favorite": 0.08, "default": 0.25,
                        "popular": 0.15, "discover": 0.55}
POPULAR_MULT = {"favorite": 0.8, "default": 1.0, "popular": 1.8, "discover": 1.0}

GENRE_WORDS = {
    "hiphop": ("рэп", "rap", "хип-хоп", "hip-hop", "trap", "phonk", "фонк"),
    "rock": ("рок", "rock", "punk", "панк", "метал", "metal"),
    "pop": ("поп", "pop"),
    "electronic": ("электрон", "electro", "techno", "техно", "house", "edm",
                   "drum", "dnb", "synth"),
    "jazz": ("джаз", "jazz", "блюз", "blues", "soul", "соул"),
    "classical": ("класс", "classic", "пиан", "piano", "скрип", "viol", "оркестр"),
    "indie": ("инди", "indie", "альтернатив", "alternative"),
    "shanson": ("шансон", "chanson", "бард", " bard"),
    "rnb": ("r&b", "rnb", "соул", "soul", "фанк", "funk"),
    "folk": ("фолк", "folk", "эстрад", "народн"),
}
GENRE_TITLE = {
    "hiphop": "хип-хоп", "rock": "рок", "pop": "поп", "electronic": "электронное",
    "jazz": "джаз", "classical": "классика", "indie": "инди", "shanson": "шансон",
    "rnb": "R&B", "folk": "фолк",
}
MOOD_WORDS = {
    "fun": ("весёл", "весел", "позит", "party", "вечерин"),
    "active": ("энерг", "танц", "dance", "драйв", "drive", "бит"),
    "calm": ("lo-fi", "lofi", "лофай", "спокой", "ambient", "инструм"),
    "sad": ("груст", "печал", "minor", "минор"),
    "edge": ("жёстк", "жестк", "агресс", "hard", "панк"),
}
MOOD_WHY = {
    "fun": "весёлое — под настроением «весельё»",
    "active": "энергичное — как в формуле",
    "calm": "спокойное — как в формуле",
    "sad": "лиричное — под настроение грусти",
    "edge": "жёсткое — под настроением фурьё",
}
ACTIVITY_WORDS = {
    "work": ("фокус", "concentr", "работ", "lo-fi", "lofi"),
    "sport": ("спорт", "качал", "мотив", "motiv", "run", "энерг"),
    "road": ("дорог", "драйв", "авто", "drive", "road"),
    "walk": ("прогул", "walk", "шум"),
    "party": ("вечерин", "тус", "party", "клуб"),
    "evening": ("вечер", "ночь", "night", "уют"),
}


def artist_key(artist: str) -> str:
    """Нормализация артиста для аффинити: «Хаски», «oxxxymiron», до запятой/feat."""
    head = (artist or "").split(",")[0].strip().lower()
    for junk in ("feat.", "ft.", "×", " x "):
        if junk in head:
            head = head.split(junk)[0].strip()
    return head


def _word_hit(text: str, words) -> bool:
    low = (text or "").lower()
    return any(w in low for w in words if w)


def is_ru_track(t: Track) -> bool:
    """Эвристика языка: кириллица в артисте/названии = русский трек."""
    text = " ".join((t.artist or "", t.title or ""))
    return any(0x0400 <= ord(c) <= 0x04FF for c in text)


def _lang_fit(t: Track, ws) -> tuple[float, str | None]:
    """Формула волны задаёт язык (ru / non-ru / any) — мягкий бонус/штраф."""
    lang = (getattr(ws, "language", "any") or "any")
    if lang in (None, "any", ""):
        return 0.0, None
    ru = is_ru_track(t)
    if lang == "ru":
        return (LANG_BONUS, "русский — язык из твоей формулы") if ru \
            else (-LANG_PENALTY, None)
    if lang == "non-ru":
        return (LANG_BONUS, "иностранный — язык из твоей формулы") if not ru \
            else (-LANG_PENALTY, None)
    return 0.0, None


def _formula_artist_keys(ws) -> set[str]:
    keys = set()
    for a in (getattr(ws, "artists", None) or []):
        name = a.get("name") if isinstance(a, dict) else a
        k = artist_key(name or "")
        if k:
            keys.add(k)
    return keys


# ------------------------------ ядро вкуса юзера ------------------------------

@dataclass
class Taste:
    likes: set[int] = field(default_factory=set)
    artist_plays: dict[str, int] = field(default_factory=dict)
    skip_ids: set[int] = field(default_factory=set)
    recent: set[int] = field(default_factory=set)          # играло за DAYS_NO_REPEAT
    disliked: set[int] = field(default_factory=set)
    disliked_artists: set[str] = field(default_factory=set)


async def taste(s: AsyncSession, user_id: int) -> Taste:
    t = Taste()
    # лайки
    q = (select(PlaylistTrack.track_id)
         .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
         .where(Playlist.owner_id == user_id, Playlist.title.like("❤%")))
    t.likes = {int(x) for x in (await s.execute(q)).scalars()}

    # история прослушиваний
    q = (select(PlayEvent.track_id, PlayEvent.finished, PlayEvent.skipped,
                PlayEvent.played_seconds, PlayEvent.started_at, Track.artist)
         .join(Track, Track.id == PlayEvent.track_id)
         .where(PlayEvent.user_id == user_id)
         .order_by(PlayEvent.started_at.desc())
         .limit(300))
    now = utcnow()
    for tid, fin, skp, played, started, artist in (await s.execute(q)).all():
        if tid is None:
            continue
        if started and (now - started) <= timedelta(days=DAYS_NO_REPEAT):
            t.recent.add(int(tid))
        if skp and float(played or 0) < HARD_SKIP_SECONDS:
            t.skip_ids.add(int(tid))
        elif fin or float(played or 0) >= 60:
            ak = artist_key(artist)
            if ak:
                t.artist_plays[ak] = t.artist_plays.get(ak, 0) + 1

    # дисклайки: трек — по двойной волне не вернётся вообще, артист — 30 дней
    q = (select(TrackFeedback.track_id, Track.artist)
         .join(Track, Track.id == TrackFeedback.track_id)
         .where(TrackFeedback.user_id == user_id, TrackFeedback.kind == -1))
    for tid, artist in (await s.execute(q)).all():
        t.disliked.add(int(tid))
        ak = artist_key(artist)
        if ak:
            t.disliked_artists.add(ak)

    # лайкнутые артисты усиливают аффинити
    if t.likes:
        q = select(Track.artist).where(Track.id.in_(list(t.likes)))
        for artist in (await s.execute(q)).scalars():
            ak = artist_key(artist)
            if ak:
                t.artist_plays[ak] = t.artist_plays.get(ak, 0) + 2
    return t


# --------------------------------- кандидаты ---------------------------------

async def _pool(s: AsyncSession, user_id: int, taste: Taste,
                limit: int = 420) -> dict[int, Track]:
    """Кандидаты: популярное в каталоге + плейлисты юзера + его лайки."""
    out: dict[int, Track] = {}
    q = select(Track).order_by(Track.downloads.desc(), Track.id.desc()).limit(limit)
    for t in (await s.execute(q)).scalars():
        out[t.id] = t
    q = (select(Track)
         .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
         .join(Playlist, Playlist.id == PlaylistTrack.playlist_id)
         .where(Playlist.owner_id == user_id)
         .limit(limit))
    for t in (await s.execute(q)).scalars():
        out.setdefault(t.id, t)
    if taste.likes:
        q = select(Track).where(Track.id.in_(list(taste.likes)))
        for t in (await s.execute(q)).scalars():
            out.setdefault(t.id, t)
    return out


# ---------------------------------- скоринг ----------------------------------

def _fit_why(t: Track, ws) -> str | None:
    """Объяснение «в формулу» и малый бонус, если текст трека попадает в слова."""
    text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
    for g in (getattr(ws, "genres", None) or []):
        g = str(g)
        if _word_hit(text, GENRE_WORDS.get(g, (g,))):
            return f"жанр «{GENRE_TITLE.get(g, g)}» из твоей формулы"
    mood = getattr(ws, "mood", None)
    if mood and _word_hit(text, MOOD_WORDS.get(mood, ())):
        return MOOD_WHY.get(mood)
    for act in (getattr(ws, "activities", None) or []):
        act = str(act)
        if _word_hit(text, ACTIVITY_WORDS.get(act, ())):
            return "в тему твоего занятия"
    return None


def _score(t: Track, kb: Taste, ws, popul_mult: float = 1.0) -> tuple[float, str | None]:
    ak = artist_key(t.artist)
    why: str | None = None
    score = 0.0

    if t.id in kb.likes:
        score += W_LIKED
        why = "из твоего «Любимого»"
    plays = kb.artist_plays.get(ak, 0)
    if plays:
        score += min(1.0, plays / 6.0) * W_ARTIST
        if not why and plays >= 2:
            why = f"ты часто слушаешь {(t.artist or '').split(',')[0].strip()}"

    # артист, вписанный в формулу волны, почти наравне с аффинити
    if ak and ak in _formula_artist_keys(ws):
        score += W_FORMULA_ARTIST
        why = why or f"{(t.artist or '').split(',')[0].strip()} — артист из твоей формулы"

    fit = _fit_why(t, ws)
    if fit:
        score += W_FIT
        why = why or fit

    # язык формулы — мелкий бонус или ощутимый штраф
    langd, langwhy = _lang_fit(t, ws)
    score += langd
    why = why or langwhy

    score += min(1.0, (t.downloads or 0) / 8.0) * W_POPULAR * popul_mult
    return score, why or "подобрано по твоей формуле"


# ---------------------------------- партия ----------------------------------

async def next_batch(s: AsyncSession, user_id: int, ws, sess: WaveSession,
                     n: int = BATCH) -> list[tuple[Track, str]]:
    """Партия из n треков. Порядок: скоринг-сортировка + эксплор-слот.

    Жёсткие правила до скоринга: дисклайки/18+ убираются полностью,
    повторы сессии — нет, недавний скип — на дно."""
    kb = await taste(s, user_id)
    pool = await _pool(s, user_id, kb)

    div = (getattr(ws, "diversity", "default") or "default") or "default"
    popul_mult = POPULAR_MULT.get(div, 1.0)
    explore_share = EXPLORE_BY_DIVERSITY.get(div, EXPLORE_SHARE)

    played = [int(x) for x in (sess.played_track_ids or [])]
    played_set = set(played)
    tails: set[str] = set()
    if played:
        q = select(Track.artist).where(Track.id.in_(played[-ARTIST_COOLDOWN_TRACKS:]))
        tails = {artist_key(a) for a in (await s.execute(q)).scalars() if a}

    def usable(t: Track, strict: bool) -> bool:
        if t.id in kb.disliked or artist_key(t.artist) in kb.disliked_artists:
            return False
        if not getattr(ws, "allow_explicit", True) and t.explicit:
            return False
        if strict and t.id in played_set:
            return False
        return True

    cands: list[tuple[float, int, Track, str]] = []
    for t in pool.values():
        if not usable(t, True):
            continue
        score, why = _score(t, kb, ws, popul_mult)
        if artist_key(t.artist) in tails:
            score -= PENALTY_REPLAY
        if t.id in kb.skip_ids:
            score += SKIP_SCORE
        cands.append((score, t.id, t, why))

    cands.sort(key=lambda r: (r[0], r[1]), reverse=True)
    picked: list[tuple[Track, str]] = [(t, why) for _, _, t, why in cands[:n]]

    # эксплор-слот: в середину партии кладём «новое», если ядро вкуса достаточно живо
    if len(cands) > n * 4 and random.random() < explore_share:
        fresh = [c for c in cands[:40]
                 if c[2].id not in kb.recent and artist_key(c[2].artist) not in kb.artist_plays]
        if fresh:
            pick = random.choice(fresh[:20])
            pos = random.randint(1, max(1, n - 1)) if n > 1 else 0
            why_new = ("новое для тебя — исследуем твой вкус"
                       if div != "discover" else "широта — режим «Новое»")
            rest = [p for p in picked if p[0].id != pick[0].id]
            picked = (rest[:pos] + [(pick[2], why_new)] + rest[pos:])[:n]

    # маленький каталог/длинная сессия: разрешаем и уже игравшее (не трогая
    # лайки/дисклайки), чтобы волна никогда не замолкала
    if len(picked) < n:
        extra: list[tuple[float, int, Track, str]] = []
        for t in pool.values():
            if not usable(t, False) or any(p[0].id == t.id for p in picked):
                continue
            score, why = _score(t, kb, ws, popul_mult)
            if t.id in kb.skip_ids:
                score += SKIP_SCORE
            extra.append((score, t.id, t, "снова в волне — из твоих настойчивых"))
            if len(extra) >= n - len(picked):
                break
        extra.sort(key=lambda r: (r[0], r[1]), reverse=True)
        picked += [(t, why) for _, _, t, why in extra]

    return picked[:n]
