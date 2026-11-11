"""Context Intelligence Engine (MODULE 2): «что подходит прямо сейчас».

Сигналы и их приоритет при конфликте (§4.2):
  1. явный выбор (занятие/настроение/знакомство из UI);
  2. поведение текущей сессии (SESSION TASTE, дрейф настроения);
  3. устойчивые контекстные привычки (context_affinity, LEVEL B);
  4. долгосрочный профиль;
  5. время суток и день недели;
  6. устройство (косвенный признак, без категоричных выводов).

Сценарий — {id, title, description, palette, bias...}. Название обязано
соответствовать содержимому (§4.3): «спокойная» волна получает своё имя
только если в пуле действительно есть спокойные треки (проверка в endless).
Геолокации нет; контекст отключается флагом WaveSettings.context_enabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .taste import SessionTaste, TasteSnapshot

# --- палитры MODULE 7 (тёмные, слои: deep → mid → glow → accent → accent2) ---
PALETTES: dict[str, dict[str, str]] = {
    "foryou":     {"deep": "#1a1730", "mid": "#251d44", "glow": "#5b4a9e", "accent": "#8f7bff", "accent2": "#c9a6ff"},
    "calm":       {"deep": "#101a2e", "mid": "#142239", "glow": "#2e4a7a", "accent": "#4d7fc4", "accent2": "#8fb6e8"},
    "energy":     {"deep": "#2b0f0a", "mid": "#3a140c", "glow": "#a63a1a", "accent": "#ff6a3d", "accent2": "#ffb56b"},
    "evening":    {"deep": "#160f26", "mid": "#1f1436", "glow": "#5c2e63", "accent": "#a05ac2", "accent2": "#e39ac2"},
    "focus":      {"deep": "#0c2019", "mid": "#0f2a20", "glow": "#1e5c46", "accent": "#37a37c", "accent2": "#7fd6b5"},
    "discovery":  {"deep": "#131829", "mid": "#1a2140", "glow": "#3a5aa6", "accent": "#5ac8c8", "accent2": "#b58ae0"},
    "favorites":  {"deep": "#260f1c", "mid": "#331226", "glow": "#7a2a52", "accent": "#c9508a", "accent2": "#ff9ec4"},
    "road":       {"deep": "#101826", "mid": "#152238", "glow": "#2a4e78", "accent": "#4aa3d8", "accent2": "#9fd0ff"},
}

MOOD_LABEL = {"fun": "весёлое", "active": "энергичное", "calm": "спокойное",
              "sad": "меланхоличное", "edge": "жёсткое"}
ACTIVITY_LABEL = {"work": "работа", "study": "учёба", "rest": "отдых", "walk": "прогулка",
                  "road": "дорога", "sport": "тренировка", "reading": "чтение",
                  "sleep": "засыпаю", "party": "вечеринка"}
FAMILIARITY_LABEL = {"favorite": "любимое", "familiar": "знакомое",
                     "similar": "что-то похожее", "new": "новое",
                     "unknown": "неизвестное", "experimental": "экспериментальное"}
DEVICE_LABEL = {"phone": "смартфон", "desktop": "компьютер", "tablet": "планшет", "tv": "ТВ"}

# занятие → настроения-биасы (по умолчанию, если юзер не выбрал настроение)
ACTIVITY_MOOD_BIAS = {
    "work": "calm", "study": "calm", "reading": "calm", "sleep": "calm",
    "sport": "active", "party": "fun", "road": "active", "walk": None,
    "rest": None,
}

# время суток → лёгкий настроенческий уклон (косвенный признак §4.2)
TIME_MOOD_BIAS = {"early_morning": "calm", "morning": "active", "day": None,
                  "evening": "calm", "late_night": "calm"}

FAMILIARITY_EXPLORE = {
    "favorite": 0.06, "familiar": 0.10, "similar": 0.25,
    "new": 0.45, "unknown": 0.60, "experimental": 0.65,
}


def time_of_day(hour: int) -> str:
    h = max(0, min(23, int(hour or 0)))
    if 5 <= h < 8:
        return "early_morning"
    if 8 <= h < 12:
        return "morning"
    if 12 <= h < 18:
        return "day"
    if 18 <= h < 23:
        return "evening"
    return "late_night"


def weekday_kind(weekday: int) -> str:
    """ISO weekday (1=Пн .. 7=Вс)."""
    return "weekend" if int(weekday) in (6, 7) else "workday"


@dataclass
class Context:
    """Нормализованный контекст сессии волны."""
    time_of_day: str = "day"
    weekday: str = "workday"
    device: str | None = None
    activity: str | None = None          # явное занятие
    mood: str | None = None              # явное настроение
    familiarity: str | None = None       # явная степень знакомства
    enabled: bool = True                 # контекстная персонализация
    session: SessionTaste | None = None
    snapshot: TasteSnapshot | None = None
    engine_mood: str | None = None       # mood из WaveSettings (формула)
    history: list[dict] = field(default_factory=list)  # последние решения (для объяснимости)
    scenario: "Scenario | None" = None   # активный сценарий (заполняется pick_scenario)
    recent_skips: list = field(default_factory=list)   # [{track_id, class}] для штрафов
    recent_tracks: list = field(default_factory=list)  # Track[] хвоста потока (усталость)
    recently_played_ids: set = field(default_factory=set)  # треки за days_no_repeat (freshness)
    recent_48h_ids: set = field(default_factory=set)   # треки за 48ч (жёсткий repeat-штраф)

    def to_json(self) -> dict:
        return {
            "time_of_day": self.time_of_day, "weekday": self.weekday,
            "device": self.device, "activity": self.activity,
            "mood": self.mood, "familiarity": self.familiarity,
            "enabled": self.enabled,
        }

    def effective_mood(self) -> str | None:
        """Приоритет: явное настроение → занятие-биас → время суток → формула."""
        if self.mood:
            return self.mood
        if self.activity and ACTIVITY_MOOD_BIAS.get(self.activity):
            return ACTIVITY_MOOD_BIAS[self.activity]
        bias = TIME_MOOD_BIAS.get(self.time_of_day)
        if bias and self.enabled:
            return bias
        return self.engine_mood or None

    def explain(self) -> str:
        parts = []
        tod = {"early_morning": "раннее утро", "morning": "утро", "day": "день",
               "evening": "вечер", "late_night": "поздняя ночь"}.get(self.time_of_day)
        if tod:
            parts.append(tod)
        if self.weekday == "weekend":
            parts.append("выходной")
        if self.activity:
            parts.append(ACTIVITY_LABEL.get(self.activity, self.activity))
        if self.mood:
            parts.append(MOOD_LABEL.get(self.mood, self.mood))
        return " · ".join(parts) if parts else "без контекста"


@dataclass
class Scenario:
    """Сценарий волны: имя обязано соответствовать фактическому содержимому."""
    id: str
    title: str
    description: str
    palette: str = "foryou"
    moods: frozenset = frozenset()          # настроенческий биас (словарные ключи)
    genres: frozenset = frozenset()         # жанровый биас
    activities: frozenset = frozenset()
    explore: float | None = None            # переопределение доли открытий
    familiarity: str | None = None
    needs_mood_check: frozenset = frozenset()  # для честности названия

    def to_json(self) -> dict:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "palette": self.palette,
            "colors": PALETTES.get(self.palette, PALETTES["foryou"]),
            "explore": self.explore,
            "familiarity": self.familiarity,
        }


def scenario_from_formula(ws) -> Scenario | None:
    """Явная формула юзера (настроение/занятие/жанры) — честное название."""
    moods = {m for m in ((getattr(ws, "mood", None),) if getattr(ws, "mood", None) else ())}
    genres = {str(g) for g in (getattr(ws, "genres", None) or [])[:4]}
    activities = {str(a) for a in (getattr(ws, "activities", None) or [])[:3]}
    if not (moods or genres or activities):
        return None
    parts = []
    for m in moods:
        parts.append(MOOD_LABEL.get(m, m))
    for g in sorted(genres)[:2]:
        parts.append({"hiphop": "хип-хоп", "rock": "рок", "pop": "поп",
                      "electronic": "электронное", "jazz": "джаз",
                      "classical": "классика", "indie": "инди",
                      "shanson": "шансон", "rnb": "R&B", "folk": "фолк"}.get(g, g))
    title = " · ".join(parts) if parts else "Формула"
    palette = "energy" if moods & {"active", "fun", "edge"} else \
        "focus" if activities & {"work", "study", "reading"} else \
        "evening" if activities & {"evening"} or (moods & {"calm", "sad"}) else "foryou"
    return Scenario(id="formula", title=title,
                    description="собрано по твоей формуле",
                    palette=palette, moods=frozenset(moods), genres=frozenset(genres),
                    activities=frozenset(activities),
                    needs_mood_check=frozenset(moods))


# --- готовые сценарии (карточки); полный динамический набор ниже ---
_BASE_SCENARIOS = (
    ("foryou", "Для тебя", "личная волна под твой вкус", "foryou"),
    ("favorites", "Любимое звучание", "то, что ты слушаешь чаще всего", "favorites"),
    ("discovery", "Открытия", "новые имена рядом с твоим вкусом", "discovery"),
    ("focus", "Спокойная концентрация", "мягкий фон для работы и учёбы", "focus"),
    ("energy", "Энергия", "драйв для тренировки и дороги", "energy"),
    ("evening", "Вечерний ритм", "вечернее настроение", "evening"),
)

_SCENARIO_BIAS = {
    "favorites": {"moods": frozenset(), "explore": 0.06},
    "discovery": {"explore": 0.55},
    "focus": {"moods": frozenset({"calm"}), "activities": frozenset({"work", "study", "reading"})},
    "energy": {"moods": frozenset({"active", "fun"}), "activities": frozenset({"sport", "party", "road"})},
    "evening": {"moods": frozenset({"calm", "sad"}), "explore": 0.15},
}


def scenario_list(ctx: Context, snap: TasteSnapshot | None) -> list[Scenario]:
    """Карточки сценариев: динамически по профилю и контексту (§2.3)."""
    out: list[Scenario] = []
    explicit = None
    if ctx.activity or ctx.mood or ctx.familiarity:
        moods = {ctx.mood} if ctx.mood else set()
        if not moods and ctx.activity and ACTIVITY_MOOD_BIAS.get(ctx.activity):
            moods = {ACTIVITY_MOOD_BIAS[ctx.activity]}
        title_parts = []
        if ctx.activity:
            title_parts.append(ACTIVITY_LABEL.get(ctx.activity, ctx.activity))
        if ctx.mood:
            title_parts.append(MOOD_LABEL.get(ctx.mood, ctx.mood))
        title = " · ".join(title_parts) or FAMILIARITY_LABEL.get(
            ctx.familiarity or "", "Под момент")
        explicit = Scenario(
            id="context", title=title,
            description="под твоё занятие и настроение" if (ctx.activity or ctx.mood)
                        else "под выбранную степень знакомства",
            palette="energy" if moods & {"active", "fun", "edge"} else
                    "focus" if ctx.activity in {"work", "study", "reading", "sleep"} else
                    "evening" if ctx.mood in {"calm", "sad"} else "road",
            moods=frozenset(m for m in moods if m),
            activities=frozenset({ctx.activity} if ctx.activity else set()),
            familiarity=ctx.familiarity,
            explore=FAMILIARITY_EXPLORE.get(ctx.familiarity or "") if ctx.familiarity else None,
            needs_mood_check=frozenset(m for m in moods if m),
        )
        out.append(explicit)

    strong = bool(snap and snap.levels.strong_artists)
    for sid, title, desc, palette in _BASE_SCENARIOS:
        bias = _SCENARIO_BIAS.get(sid, {})
        if sid == "favorites" and not strong:
            continue                      # нет LEVEL A — карточка не обещает
        if sid == "discovery" and snap and snap.explore_share <= 0.10:
            continue
        if sid == "energy" and ctx.time_of_day == "late_night":
            continue
        if sid == "focus" and ctx.time_of_day in ("late_night",):
            continue
        out.append(Scenario(
            id=sid, title=title, description=desc, palette=palette,
            moods=bias.get("moods", frozenset()),
            activities=bias.get("activities", frozenset()),
            explore=bias.get("explore"),
            needs_mood_check=bias.get("moods", frozenset()),
        ))
    return out[:6]


def pick_scenario(ctx: Context, snap: TasteSnapshot | None,
                  requested: str | None = None) -> Scenario:
    """Выбор активного сценария: явный запрос → контекстные карточки → «Для тебя»."""
    candidates = scenario_list(ctx, snap)
    if requested:
        for sc in candidates:
            if sc.id == requested:
                return sc
    return candidates[0] if candidates else Scenario(
        id="foryou", title="Для тебя", description="личная волна под твой вкус")


def mood_of_texts(tracks, moods: frozenset) -> int:
    """Сколько треков подтверждают заявленные настроения (честность названия)."""
    from services.wave.own_engine import MOOD_WORDS
    if not moods:
        return 10 ** 6
    hit = 0
    for t in tracks:
        text = " ".join((t.title or "", t.artist or "", t.album or "")).lower()
        if any(any(w in text for w in MOOD_WORDS.get(m, ())) for m in moods):
            hit += 1
    return hit
