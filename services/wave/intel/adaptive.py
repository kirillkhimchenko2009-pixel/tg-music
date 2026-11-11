"""Adaptive Optimization (MODULE 8, этапы 11–12): веса под реальное поведение.

Никакой магии «самообучающегося ML»: прозрачные правила над наблюдаемыми
срезами, срабатывают только когда данных достаточно (порог в конфиге
WaveKV-секции "ml"), малыми шагами и не чаще cooldown. Не оптимизируем по
длительности как цели: сигнал — дослушивания/скипы/дисклайки.

Правила (§4.4, объяснимые):
  context_fit ↑  если плеи с активным сценарием/занятием скипаются РЕЖЕ обычных
  context_fit ↓  если скипаются ЧАЩЕ — контекст не помогает, вес честно падает
  discovery   ↑  если плеи новых артистов дослушивают чаще среднего по юзеру
  discovery   ↓  если новые артисты чаще дослушиваний получают дисклайки

Хранение: личные веса — WaveKV "weights:u<id>" (поверх глобальных, слои в
config.load_config); аудит — WaveKV "ml:u<id>" (когда, сколько событий, какие
сдвиги). Сброс — /wave/clear scope taste|all (приватность §20).
"""
from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import ArtistStats, WaveEvent, WaveKV, utcnow
from .config import DEFAULT_ML, DEFAULT_WEIGHTS, load_section

_STATE_KEY = "ml:u{}"
_WEIGHTS_KEY = "weights:u{}"

# сценарий по умолчанию — не «контекст»: срез для сравнения нужен явный
_DEFAULT_SCENARIOS = ("", "foryou")


def _is_context(e: WaveEvent) -> bool:
    sc = (e.scenario or "").strip().lower()
    return (sc not in _DEFAULT_SCENARIOS) or bool((e.context or {}).get("activity"))


def _rates(evts: list[WaveEvent], skipped: set, completed: set,
           disliked: set) -> dict:
    n = len(evts)
    if not n:
        return {"starts": 0, "skip_rate": 0.0, "completion_rate": 0.0,
                "dislike_rate": 0.0}
    sk = sum(1 for e in evts if e.track_id in skipped)
    cp = sum(1 for e in evts if e.track_id in completed)
    ds = sum(1 for e in evts if e.track_id in disliked)
    return {"starts": n, "skip_rate": sk / n, "completion_rate": cp / n,
            "dislike_rate": ds / n}


async def _observe(s: AsyncSession, user_id: int, window_days: float) -> dict:
    """Срезы поведения: контекстные/обычные плеи, открытия/все старты."""
    since = utcnow() - timedelta(days=window_days)
    q = (select(WaveEvent)
         .where(WaveEvent.user_id == user_id, WaveEvent.created_at >= since)
         .order_by(WaveEvent.created_at.desc()).limit(5000))
    rows = (await s.execute(q)).scalars().all()
    started = [e for e in rows if e.kind == "track_started"]
    skipped = {e.track_id for e in rows if e.kind == "skipped" and e.track_id}
    completed = {e.track_id for e in rows if e.kind == "completed" and e.track_id}
    disliked = {e.track_id for e in rows if e.kind == "disliked" and e.track_id}
    discovered = {x for x in (await s.execute(
        select(ArtistStats.artist_key)
        .where(ArtistStats.user_id == user_id,
               ArtistStats.discovered_via.isnot(None)))).scalars()}
    ctx = [e for e in started if _is_context(e)]
    plain = [e for e in started if not _is_context(e)]
    disc = [e for e in started if e.artist_key and e.artist_key in discovered]
    return {"events": len(rows),
            "ctx": _rates(ctx, skipped, completed, disliked),
            "plain": _rates(plain, skipped, completed, disliked),
            "discovery": _rates(disc, skipped, completed, disliked),
            "base": _rates(started, skipped, completed, disliked)}


def _renormalize(w: dict[str, float], floor: float) -> None:
    """Сумма весов возвращается к 1, каждый не ниже floor."""
    for k in w:
        w[k] = max(floor, float(w[k]))
    total = sum(w.values()) or 1.0
    for k in w:
        w[k] = round(w[k] / total, 4)


async def _load_json(s: AsyncSession, key: str) -> dict:
    row = (await s.execute(select(WaveKV).where(WaveKV.key == key))
           ).scalar_one_or_none()
    return row.value if row and isinstance(row.value, dict) else {}


async def _save_json(s: AsyncSession, key: str, value: dict) -> None:
    row = (await s.execute(select(WaveKV).where(WaveKV.key == key))
           ).scalar_one_or_none()
    if row is None:
        s.add(WaveKV(key=key, value=value))
    else:
        row.value = value


async def user_weights(s: AsyncSession, user_id: int) -> dict:
    """Личный оверлей весов (для показа в метриках); {} если адаптации не было."""
    return await _load_json(s, _WEIGHTS_KEY.format(int(user_id)))


async def audit(s: AsyncSession, user_id: int) -> dict:
    """Последняя проверка/адаптация (для метрик §17)."""
    return await _load_json(s, _STATE_KEY.format(int(user_id)))


async def reset(s: AsyncSession, user_id: int) -> None:
    """Приватность: убрать личные веса и аудит адаптации."""
    for key in (_WEIGHTS_KEY, _STATE_KEY):
        row = (await s.execute(select(WaveKV).where(
            WaveKV.key == key.format(int(user_id))))).scalar_one_or_none()
        if row is not None:
            await s.delete(row)


async def maybe_adapt(s: AsyncSession, user_id: int) -> dict | None:
    """Проверить данные и (если порог пройден) подстроить веса юзера.

    Возвращает аудит-словарь или None (кулдаун/мало данных — проверка всё
    равно запоминается, чтобы не пересчитывать на каждом событии).
    """
    ml = await load_section(s, "ml", DEFAULT_ML)
    state = await _load_json(s, _STATE_KEY.format(int(user_id)))
    at = state.get("at")
    if at:
        try:
            if isinstance(at, str):
                from datetime import datetime as _dt
                at = _dt.fromisoformat(at)
            age = (utcnow() - at).total_seconds()
            if 0 <= age < float(ml["cooldown_hours"]) * 3600.0:
                return None
        except (TypeError, ValueError):
            pass

    obs = await _observe(s, int(user_id), float(ml["window_days"]))
    report: dict = {"at": utcnow().isoformat(), "events": obs["events"],
                    "changes": {}}
    if obs["events"] < float(ml["min_events"]):
        await _save_json(s, _STATE_KEY.format(int(user_id)), report)
        return None

    # текущие личные веса (поверх глобальных) — база для сдвига
    w = dict(await load_section(s, "weights", DEFAULT_WEIGHTS))
    personal = await _load_json(s, _WEIGHTS_KEY.format(int(user_id)))
    for k, v in personal.items():
        if k in w and isinstance(v, (int, float)):
            w[k] = float(v)

    step = float(ml["step"])
    floor = float(ml["min_weight"])
    min_slice = float(ml["min_slice"])

    # 1) context_fit: сравниваем скипы контекстных и обычных плеев
    cs, ps = obs["ctx"], obs["plain"]
    if cs["starts"] >= min_slice and ps["starts"] >= min_slice:
        d = step * math.tanh((ps["skip_rate"] - cs["skip_rate"]) / 0.2)
        if abs(d) > 1e-4:
            w["context_fit"] = max(floor, w.get("context_fit", 0.0) + d)
            report["changes"]["context_fit"] = round(d, 4)
            report["rates"] = {"ctx_skip": round(cs["skip_rate"], 3),
                               "plain_skip": round(ps["skip_rate"], 3)}

    # 2) discovery: дослушивания открытий vs база, минус лишние дисклайки
    ds, base = obs["discovery"], obs["base"]
    if ds["starts"] >= min_slice and base["starts"] >= min_slice:
        d = (step * math.tanh((ds["completion_rate"] - base["completion_rate"]) / 0.2)
             - 0.5 * step * math.tanh(
                 (ds["dislike_rate"] - base["dislike_rate"]) / 0.1))
        d = max(-step, min(step, d))
        if abs(d) > 1e-4:
            w["discovery"] = max(floor, w.get("discovery", 0.0) + d)
            report["changes"]["discovery"] = round(d, 4)
            report.setdefault("rates", {}).update(
                {"disc_completion": round(ds["completion_rate"], 3),
                 "base_completion": round(base["completion_rate"], 3)})

    if report["changes"]:
        _renormalize(w, floor)
        await _save_json(s, _WEIGHTS_KEY.format(int(user_id)), w)
        report["weights"] = dict(w)
    await _save_json(s, _STATE_KEY.format(int(user_id)), report)

    from .config import invalidate
    invalidate()                      # слой weights:u<id> мог измениться
    return report if report["changes"] else None
