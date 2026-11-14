"""Сетевые утилиты: единая точка прокси для httpx, yt-dlp и spotipy."""
import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx

from core.config import settings

def yt_dlp_binary() -> str:
    """Путь к yt-dlp: venv → PATH → fallback имя."""
    cand = None
    try:
        venv_exe = Path(sys.executable).resolve().parent / "yt-dlp.exe"
        if venv_exe.exists():
            cand = str(venv_exe)
    except Exception:
        cand = None
    return cand or shutil.which("yt-dlp") or "yt-dlp"

def yt_dlp_proxy_args() -> list[str]:
    return ["--proxy", settings.PROXY_URL] if settings.PROXY_URL else []

def httpx_client(**kw) -> httpx.AsyncClient:
    kw.setdefault("timeout", 30)
    kw.setdefault("proxy", settings.PROXY_URL or None)
    kw.setdefault("headers", {})
    kw.setdefault("follow_redirects", False)
    return httpx.AsyncClient(**kw)

def httpx_sync_client(**kw) -> httpx.Client:
    """Синхронный клиент с той же прокси-конвенцией (для адаптеров, чья
    внутренняя кухня синхронная и оборачивается asyncio.to_thread)."""
    kw.setdefault("timeout", 20)
    kw.setdefault("proxy", settings.PROXY_URL or None)
    kw.setdefault("headers", {})
    kw.setdefault("follow_redirects", True)
    return httpx.Client(**kw)

def spotipy_session() -> "object":
    """requests.Session с прокси (для spotipy)."""
    import requests
    s = requests.Session()
    if settings.PROXY_URL:
        s.proxies.update({"http": settings.PROXY_URL, "https": settings.PROXY_URL})
    return s


def ympx_key() -> str:
    """Общий ключ для /ympx (мини-прокси ЯМ на локальном русском сервере).

    Бот-токен один и тот же на обеих сторонах (локально и на проде), поэтому
    отдельный секрет не нужен: обе стороны выводят ключ из своего BOT_TOKEN."""
    import hashlib
    return hashlib.sha256((settings.BOT_TOKEN + "|vibe-ympx-v1")
                          .encode()).hexdigest()[:32]

async def yt_dlp_search(query: str, limit: int = 8) -> list[dict]:
    """Гостевой поиск через yt-dlp (работает даже без ключей; прокси учитывается)."""
    cmd = [
        yt_dlp_binary(), "--flat-playlist", "--dump-json", "--no-warnings", "--quiet",
        *yt_dlp_proxy_args(),
        f"ytsearch{int(limit)}:{query}",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        # yt-dlp на чёрной дыре сети висит бесконечно — потолок 45 с, потом kill
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return []
    except Exception:
        return []
    items = []
    for line in out.decode(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("id") and e.get("title"):
            items.append(e)
    return items

def cover_from_yt(entry: dict) -> str | None:
    thumbs = entry.get("thumbnails") or []
    if thumbs and isinstance(thumbs, list):
        return thumbs[-1].get("url")
    return None
