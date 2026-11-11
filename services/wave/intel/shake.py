"""Shake Engine (MODULE 10): «встряхнуть волну».

При встряхивании меняется СЦЕНАРИЙ, а не личность пользователя (§8.1):
долгосрочный профиль не трогаем. Выбираем альтернативу, меняя 1–2 оси
(энергия, настроение, знакомство, жанр, доля новых) от текущего сценария,
с cooldown против дребезга и без повторов недавних встряхиваний.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .context import (Context, Scenario, MOOD_LABEL, PALETTES,
                      FAMILIARITY_EXPLORE, scenario_list)

# cooldown встряхивания на процесс (защита от параллельных запросов и дребезга)
SHAKE_COOLDOWN_S = 2.5
_last_shake: dict[int, float] = {}
_last_ids: dict[int, list[str]] = {}

# оси, которые может тронуть встряхивание
MOOD_ALTERNATIVES = {
    "active": ("calm", "fun", "edge"),
    "fun": ("active", "calm"),
    "calm": ("active", "sad"),
    "sad": ("calm", "fun"),
    "edge": ("calm", "active"),
    None: ("active", "calm", "fun"),
}

FAMILIARITY_ALTERNATIVES = {
    None: ("similar", "new", "favorite"),
    "favorite": ("similar", "new"),
    "familiar": ("new", "similar"),
    "similar": ("new", "favorite"),
    "new": ("favorite", "similar"),
    "unknown": ("favorite", "similar"),
    "experimental": ("favorite", "new"),
}


def shake_allowed(user_id: int) -> bool:
    now = time.monotonic()
    if now - _last_shake.get(user_id, 0.0) < SHAKE_COOLDOWN_S:
        return False
    _last_shake[user_id] = now
    return True


def shake_done(user_id: int) -> None:
    """После успешного ответа можно снять пометку (или оставить как cooldown)."""


@dataclass
class ShakeResult:
    scenario: Scenario
    changed: list[str]                  # какие оси поменялись
    note: str                           # описание нового сценария для UI


def _remember(user_id: int, scenario_id: str) -> None:
    ids = _last_ids.setdefault(user_id, [])
    ids.append(scenario_id)
    _last_ids[user_id] = ids[-4:]


def recent_shakes(user_id: int) -> list[str]:
    return list(_last_ids.get(user_id, []))


def alternate_scenario(ctx: Context, user_id: int = 0) -> ShakeResult:
    """Альтернативный сценарий от текущего: меняем 1–2 оси, не вкус."""
    cur: Scenario | None = ctx.scenario
    changed: list[str] = []

    # 1) настроение: от текущего эффективного — в соседнюю по энергии сторону
    cur_mood = None
    if cur and cur.moods:
        cur_mood = next(iter(cur.moods))
    if not cur_mood:
        cur_mood = ctx.effective_mood()
    options = [m for m in MOOD_ALTERNATIVES.get(cur_mood, ("calm", "active"))
               if m != cur_mood]
    new_mood = random.choice(options) if options else None

    # 2) знакомство: сдвиг «выше по спирали» или «к любимому»
    cur_fam = ctx.familiarity or (cur.familiarity if cur else None)
    new_fam = random.choice(FAMILIARITY_ALTERNATIVES.get(cur_fam, ("similar", "new")))
    if new_fam != cur_fam:
        changed.append("знакомство")

    if new_mood and new_mood != cur_mood:
        changed.append("настроение")
    else:
        new_mood = cur_mood            # mood мог не поменяться — меняем explore

    # 3) доля открытий из знакомства (или усиление, если оси совпали)
    explore = FAMILIARITY_EXPLORE.get(new_fam) if new_fam else None

    title_parts = []
    if new_mood:
        title_parts.append(MOOD_LABEL.get(new_mood, new_mood))
    title = " · ".join(title_parts) or "Другой срез"

    palette = "energy" if new_mood in ("active", "fun", "edge") else \
        "evening" if new_mood in ("calm", "sad") else "discovery"
    if new_fam in ("new", "unknown", "experimental") and palette == "energy":
        palette = "discovery"

    scen = Scenario(
        id=f"shake:{int(time.time())}",
        title=f"Встряска: {title}",
        description="сменил " + " и ".join(changed) if changed else
                    "другой срез твоей волны",
        palette=palette,
        moods=frozenset({new_mood} if new_mood else set()),
        familiarity=new_fam,
        explore=explore,
        needs_mood_check=frozenset({new_mood} if new_mood else set()),
    )
    _remember(int(user_id or 0), scen.id)
    return ShakeResult(scenario=scen, changed=changed or ["срез волны"],
                       note=scen.description)
