"""Конфигурация рекомендательного движка, отделённая от кода.

Дефолты — константы здесь; поверх них — WaveKV (таблица wave_kv): ключ
"weights" переопределяет веса скоринга, "limits" — жёсткие лимиты,
"explore" — баланс знакомого/нового. Правка — через запись в WaveKV,
код трогать не нужно. Всё читается с кэшем на процесс (TTL), чтобы
не ходить в БД на каждый трек.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, fields, replace

# --- веса скоринга (стартовая модель §5.3, нормализованы) ---
DEFAULT_WEIGHTS: dict[str, float] = {
    "taste_fit": 0.30,
    "context_fit": 0.25,
    "session_fit": 0.15,
    "discovery": 0.12,
    "audio_quality": 0.08,
    "freshness": 0.10,
}

# --- штрафы (множители вычитаемой части) ---
DEFAULT_PENALTY: dict[str, float] = {
    "repeat": 0.60,        # трек играл в сессии / за последние N дней
    "dislike_track": 3.0,  # дисклайкнутый трек — за порог отсечения
    "dislike_artist": 1.2, # артист в моратории после дисклайка
    "artist_fatigue": 0.55,  # артист уже звучал в последних треках потока
    "skip_recent": 0.35,   # недавний скип этого трека
}

# --- жёсткие лимиты ---
DEFAULT_LIMITS: dict[str, float] = {
    "hard_skip_seconds": 25.0,     # скип быстрее = «не хочу» (совместимо с движком B)
    "soft_skip_seconds": 10.0,     # мгновенный скип — сильнее обычного
    "near_end_share": 0.85,        # скип после 85% — «дослушал, не тянуло»
    "artist_cooldown_tracks": 4,   # артист не повторяется N треков подряд
    "days_no_repeat": 7,           # трек отдыхает N дней
    "skip_freeze_days": 3,         # скипнутый трек не возвращается N дней
    "dislike_artist_days": 30,     # мораторий артиста после дисклайка
    "batch": 3,                    # размер партии волны
    "min_completion_positive": 60.0,   # дослушал >= N секунд — позитивный сигнал
    "profile_refresh_minutes": 360,    # период фонового пересчёта профиля
    "cf_min_users": 2,             # k-анонимность для co-occurrence источника
    "session_minutes": 30,         # окно SESSION TASTE
}

# --- баланс знакомого/нового (§5.4) ---
DEFAULT_EXPLORE: dict[str, float] = {
    "default": 0.20,
    "favorite": 0.08,
    "popular": 0.15,
    "discover": 0.55,
    "min": 0.10,           # открытий нельзя выжечь совсем
    "max": 0.35,           # и не может стать рулеткой
}

# --- адаптация весов (этапы 11–12): порог данных, шаг, кулдаун ---
DEFAULT_ML: dict[str, float] = {
    "min_events": 300.0,   # минимум событий юзера в окне, иначе не трогаем веса
    "min_slice": 25.0,     # минимум стартов в сравниваемом срезе
    "window_days": 30.0,   # окно наблюдения
    "step": 0.02,          # максимальный сдвиг одного веса за адаптацию
    "cooldown_hours": 24.0,   # не чаще раза в сутки
    "min_weight": 0.03,    # вес не выжигается в ноль
}

TTL_SECONDS = 120.0
_cache: dict[str, tuple[float, dict]] = {}


async def _load_kv(s, key: str) -> dict | None:
    from sqlalchemy import select
    from core.models import WaveKV
    row = (await s.execute(select(WaveKV).where(WaveKV.key == key))).scalar_one_or_none()
    return row.value if row and isinstance(row.value, dict) else None


async def load_section(s, key: str, defaults: dict[str, float]) -> dict[str, float]:
    """Секция конфига: дефолты + WaveKV, с кэшем на процесс."""
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < TTL_SECONDS:
        return hit[1]
    merged = dict(defaults)
    try:
        override = await _load_kv(s, key)
        if override:
            for k, v in override.items():
                if k in merged and isinstance(v, (int, float)):
                    merged[k] = float(v)
    except Exception:        # конфиг — опция, а не критический путь
        pass
    _cache[key] = (now, merged)
    return merged


def invalidate() -> None:
    """Сброс кэша после правки WaveKV."""
    _cache.clear()


@dataclass
class WaveConfig:
    """Снимок конфига одного запроса волны: веса + штрафы + лимиты."""
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    penalty: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PENALTY))
    limits: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    explore: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_EXPLORE))
    ml: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ML))

    def with_overrides(self, s) -> "WaveConfig":
        return replace(self)


async def load_config(s, user_id: int | None = None) -> WaveConfig:
    """Глобальные секции + personal-оверлей весов юзера (адаптация, этап 12).

    Слои: дефолты кода → WaveKV "weights" (общие) → WaveKV
    "weights:u<id>" (личные, пишет только intel/adaptive.py).
    """
    cfg = WaveConfig()
    cfg.weights = await load_section(s, "weights", DEFAULT_WEIGHTS)
    cfg.penalty = await load_section(s, "penalty", DEFAULT_PENALTY)
    cfg.limits = await load_section(s, "limits", DEFAULT_LIMITS)
    cfg.explore = await load_section(s, "explore", DEFAULT_EXPLORE)
    cfg.ml = await load_section(s, "ml", DEFAULT_ML)
    if user_id is not None:
        personal = await _load_kv(s, f"weights:u{int(user_id)}")
        if isinstance(personal, dict):
            cfg.weights = dict(cfg.weights)        # кэш секции не мутируем
            for k, v in personal.items():
                if k in cfg.weights and isinstance(v, (int, float)):
                    cfg.weights[k] = float(v)
    return cfg


def clamp01(x: float) -> float:
    try:
        return min(1.0, max(0.0, float(x)))
    except (TypeError, ValueError):
        return 0.0
