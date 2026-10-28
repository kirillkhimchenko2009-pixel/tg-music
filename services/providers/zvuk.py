"""Звук (zvuk.com): поиск/плейлисты/стримы по пользовательскому токену."""
import asyncio

from services.net import httpx_client
from core.config import settings
from services.providers.base import TrackMeta

API = "https://zvuk.com/api/v1"

def headers(token: str) -> dict:
    token = token or settings.ZVUK_TOKEN
    return {"x-auth-token": token or "", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def _cover(c: dict | None) -> str | None:
    uri = (c or {}).get("template") or ""
    return f"https://zvuk.com/{uri.replace('%s', '400x400')}" if uri else None

def _parse(t: dict) -> TrackMeta | None:
    if not t.get("id"):
        return None
    artists = ", ".join((a or {}).get("title", "") for a in (t.get("artists") or []))
    rel = t.get("release") or {}
    return TrackMeta(
        source="zvuk", external_id=str(t["id"]),
        title=t.get("title") or "", artist=artists,
        album=rel.get("title"),
        year=int(rel.get("date")[:4]) if isinstance(rel.get("date"), str) and rel["date"][:4].isdigit() else None,
        duration=int(t.get("duration") or 0), cover_url=_cover(t.get("image")))

async def search(q: str, limit: int = 8, token: str | None = None) -> list[TrackMeta]:
    async with httpx_client(headers=headers(token), timeout=20) as c:
        r = await c.get(f"{API}/search", params={"limit": limit, "q": q})
        r.raise_for_status()
        data = r.json()
    return [m for m in map(_parse, ((data.get("results") or {}).get("tracks")) or []) if m]

async def get_playlist(playlist_id: str, token: str | None = None) -> tuple[str, list[TrackMeta]]:
    async with httpx_client(headers=headers(token), timeout=20) as c:
        r = await c.get(f"{API}/playlist", params={"id": playlist_id})
        r.raise_for_status()
        data = r.json().get("result") or {}
    title = data.get("title") or "Плейлист Звук"
    return title, [m for m in map(_parse, data.get("tracks") or []) if m]

async def track_meta(track_id: str, token: str | None = None) -> TrackMeta | None:
    async with httpx_client(headers=headers(token), timeout=20) as c:
        r = await c.get(f"{API}/track", params={"id": track_id})
        r.raise_for_status()
        tracks = ((r.json().get("result") or {}).get("tracks")) or []
        return _parse(tracks[0]) if tracks else None

async def stream_url(track_id: str, token: str | None = None) -> str | None:
    async with httpx_client(headers=headers(token), timeout=20) as c:
        r = await c.get(f"{API}/stream", params={"id": track_id})
        r.raise_for_status()
        res = r.json().get("result") or {}
        url = res.get("stream_url")
        if url and url.startswith("//"):
            url = "https:" + url
        return url
