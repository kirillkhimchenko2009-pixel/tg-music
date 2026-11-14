"""YMPX: мини-прокси анонимного API Яндекс.Музыки для прода.

Прод (ФРГ) не может читать анонимное API ЯМ (гео-блок 451). Если где-то
крутится сервер в РФ (например, локальный, наружу через туннель ngrok со
статическим доменом), прод ходит сюда: GET /ympx/<path> — этот сервер
отдаёт ответ ЯМ со своего российского IP.

Защита: заголовок X-Ympx-Key = sha256(BOT_TOKEN|vibe-ympx-v1)[:32]
(см. services.net.ympx_key). Открытым прокси для чужих это не делает.
На проде эндпоинт тоже существует, но бесполезен (ЯМ отдаст ему 451) —
его значение имеет только РФ-сервер.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import httpx

from core.config import settings
from services.net import httpx_client, ympx_key

router = APIRouter(tags=["ympx"])
log = logging.getLogger("ympx")

TARGET = "https://api.music.yandex.net"


@router.get("/ympx/{path:path}")
async def ympx(path: str, request: Request):
    if not settings.BOT_TOKEN:
        raise HTTPException(503, "ympx отключён (нет BOT_TOKEN)")
    if request.headers.get("x-ympx-key") != ympx_key():
        raise HTTPException(403, "ympx: плохой ключ")

    params = dict(request.query_params)
    # proxy=None обязательно: локальный PROXY_URL (иностранное выход) даст 451,
    # а смысл ympx — прямой русский IP этой машины
    async with httpx_client(proxy=None, timeout=httpx.Timeout(60.0, connect=10.0),
                            follow_redirects=True) as c:
        try:
            r = await c.get(f"{TARGET}/{path.lstrip('/')}", params=params)
        except httpx.TransportError as e:
            log.warning("ympx upstream %s: %s: %s", path, type(e).__name__,
                        str(e)[:120])
            raise HTTPException(504, f"ympx: ЯМ не ответил ({type(e).__name__})")
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))
