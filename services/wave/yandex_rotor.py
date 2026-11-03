"""Мини-клиент rotor-сессий Яндекса — движок A волны (настоящая «Моя волна»).

Контракт сверен по живому API-клиенту MarshalX/yandex-music-api
(yandex_music/_client/rotor_sessions.py, rotor/session_feedback.py):

  POST /rotor/session/new            {seeds, queue?, includeTracksInResponse, allowExplicit}
                                     -> {radioSessionId, batchId, sequence}
  POST /rotor/session/{id}/tracks    {queue, feedbacks?} -> {batchId, sequence}
  POST /rotor/session/{id}/feedback  {event: {type, timestamp, trackId?, totalPlayedSeconds?},
                                     batchId?, from}
  GET  /rotor/account/status         -> {skipsPerHour, account.plus, ...}

event.type: radioStarted (без trackId), trackStarted, trackFinished, skip, like,
            unlike, dislike, undislike (после дизлайкового семейства нужны
            totalPlayedSeconds для trackFinished/skip).
timestamp обязателен всегда: ISO 8601 «YYYY-MM-DDTHH:MM:SS.mmmZ».

Свой httpx-клиент, а не lib yandex_music: рантайму нужны 4 ручки, sync-клиент
библиотеки в async-сервере тянет лишние потоки.
"""
import logging
from datetime import datetime, timezone

from services.net import httpx_client

LOG = logging.getLogger("wave.rotor")
API = "https://api.music.yandex.net"
FROM = "vibe-wave"


class RotorError(Exception):
    """rotor недоступен/ошибся — движок A отдыхает 30 мин, играем движком B."""


def now_iso() -> str:
    """Формат ЯМ: 2026-11-02T13:45:12.123Z."""
    n = datetime.now(timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json",
        "User-Agent": "Yandex-Music-API/2026.11",
        "X-Yandex-Music-Client": "YandexMusicAndroid/2025.01.1",
        "Accept": "application/json",
    }


async def _req(method: str, path: str, token: str, body: dict | None = None):
    if not token:
        raise RotorError("нет ЯМ-токена")
    try:
        async with httpx_client(timeout=15, headers=_headers(token)) as c:
            r = await c.request(method, API + path, json=body)
    except TimeoutError as e:
        raise RotorError(f"{path}: таймаут") from e
    except Exception as e:  # httpx.* и прочая сеть
        raise RotorError(f"{path}: сеть {type(e).__name__}") from e
    if r.status_code >= 400:
        raise RotorError(f"{path} -> {r.status_code}: {r.text[:160]}")
    ct = r.headers.get("content-type", "")
    if "json" not in ct:
        return {}
    return (r.json() or {}).get("result")


# ----------------------------- сиды формулы волны -----------------------------

def formula_seeds(ws) -> list[str]:
    """Сиды rotor из нашей формулы волны (WaveSettings).

    База — сама «Моя волна» (user:onyourwave); поверх неё настройка-сиды
    (serialized_seed: settingMoodEnergy/settingDiversity/settingLanguage),
    жанры и артисты юзера. «default/any/None» не отправляем — это уже в волне.
    """
    seeds = ["user:onyourwave"]
    mood = (getattr(ws, "mood", None) or "").strip()
    if mood:
        seeds.append(f"settingMoodEnergy:{mood}")
    div = (getattr(ws, "diversity", "default") or "default").strip()
    if div and div != "default":
        seeds.append(f"settingDiversity:{div}")
    lang = (getattr(ws, "language", "any") or "any").strip()
    if lang and lang != "any":
        seeds.append(f"settingLanguage:{lang}")
    for g in (getattr(ws, "genres", None) or []):
        if str(g).strip():
            seeds.append(f"genre:{str(g).strip()}")
    for a in (getattr(ws, "artists", None) or []):
        ym = a.get("ym_id") if isinstance(a, dict) else a
        if ym:
            seeds.append(f"artist:{ym}")
    return seeds


async def account_status(token: str) -> dict:
    """skipsPerHour (лимит скипов) и признак Плюса."""
    data = await _req("GET", "/rotor/account/status", token)
    if not isinstance(data, dict):
        return {"skips_per_hour": 0, "plus": False}
    acc = data.get("account") or {}
    return {
        "skips_per_hour": int(data.get("skipsPerHour") or 0),
        "plus": bool(acc.get("plus") or data.get("plus")),
    }


def _norm_session(d: dict) -> dict:
    return {
        "radio_session_id": str((d or {}).get("radioSessionId") or ""),
        "batch_id": (d or {}).get("batchId") or None,
        "sequence": (d or {}).get("sequence") or [],
        "terminated": bool((d or {}).get("terminated")),
        "unknown_session": bool((d or {}).get("unknownSession")),
    }


async def session_new(token: str, seeds: list[str], *,
                      allow_explicit: bool | None = None,
                      queue: list[str] | None = None) -> dict:
    """Новая сессия радио. Отдаёт сразу первую партию (includeTracksInResponse)."""
    body: dict = {"seeds": seeds, "includeTracksInResponse": True}
    if allow_explicit is not None:
        body["allowExplicit"] = bool(allow_explicit)
    if queue:
        body["queue"] = queue[-30:]
    return _norm_session(await _req("POST", "/rotor/session/new", token, body))


async def session_tracks(token: str, radio_session_id: str, queue: list[str]) -> dict:
    """Следующая партия: queue — уже отгруженные треки (id или id:album)."""
    data = await _req("POST", f"/rotor/session/{radio_session_id}/tracks", token,
                      {"queue": queue[-30:]})
    return _norm_session(data)


def event(kind: str, track_id: str | None = None,
          played_seconds: float | None = None) -> dict:
    """Событие фидбека SessionEvent: timestamp обязателен всегда."""
    e: dict = {"type": kind, "timestamp": now_iso()}
    if track_id:
        e["trackId"] = str(track_id)
    if played_seconds is not None:
        e["totalPlayedSeconds"] = round(max(0.0, float(played_seconds)), 2)
    return e


async def session_feedback(token: str, radio_session_id: str, ev: dict,
                           batch_id: str | None = None) -> bool:
    """Одиночный фидбек SessionFeedback = {event, batchId?, from}."""
    body: dict = {"event": ev, "from": FROM}
    if batch_id:
        body["batchId"] = batch_id
    await _req("POST", f"/rotor/session/{radio_session_id}/feedback", token, body)
    return True
