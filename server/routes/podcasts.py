"""Public podcast RSS catalog and safe, ranged audio proxy for the Mini App."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/podcasts", tags=["podcasts"])

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
FEEDS = {
    "zavtracast": {
        "title": "Завтракаст", "category": "Культура",
        "description": "Большой разговор о технологиях, играх и культуре.",
        "feed": "https://zavtracast.ru/podcast/feed",
        "hosts": {"media.blubrry.com", "zavtracast.ru"},
        "color": "#8f69d4",
    },
    "radio-t": {
        "title": "Радио-Т", "category": "Технологии",
        "description": "Еженедельный разговор о технологиях и интернете.",
        "feed": "https://radio-t.com/podcast.rss",
        "hosts": {"cdn.radio-t.com", "radio-t.com"},
        "color": "#56a6c8",
    },
    "umputun": {
        "title": "Умпутун", "category": "Разговоры",
        "description": "Архив авторских аудиоэфиров и разговорных программ.",
        "feed": "https://podcast.umputun.com/podcast.rss",
        "hosts": {"podcast.umputun.com", "feeds.rucast.net"},
        "color": "#d28d55",
    },
}
_CACHE_TTL = 600
_MAX_FEED_BYTES = 4 * 1024 * 1024
_MAX_EPISODES = 40
_MAX_AUDIO_BYTES = 300 * 1024 * 1024
_catalog_cache: tuple[float, list[dict]] | None = None
_catalog_lock = asyncio.Lock()


def _text(node: ET.Element, name: str) -> str:
    return (node.findtext(name) or "").strip()


def _duration(value: str) -> int:
    try:
        parts = [int(x) for x in value.split(":")]
        if not 1 <= len(parts) <= 3:
            return 0
        total = 0
        for part in parts:
            if part < 0:
                return 0
            total = total * 60 + part
        return total
    except (TypeError, ValueError):
        return 0


def _published(value: str) -> tuple[str, float]:
    if not value:
        return "", 0
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat(), stamp.timestamp()
    except (TypeError, ValueError, OverflowError):
        return "", 0


def _image(channel: ET.Element) -> str:
    image = channel.find(f"{ITUNES}image")
    if image is not None and image.get("href"):
        return image.get("href", "")
    node = channel.find("image/url")
    return (node.text or "").strip() if node is not None else ""


def _parse_feed(show_id: str, xml: bytes) -> dict:
    upper_xml = xml.upper()
    if len(xml) > _MAX_FEED_BYTES or b"<!DOCTYPE" in upper_xml or b"<!ENTITY" in upper_xml:
        raise ValueError("RSS feed is too large or contains a DTD")
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel not found")
    config = FEEDS[show_id]
    episodes = []
    for item in channel.findall("item")[:_MAX_EPISODES]:
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        audio_url = (enclosure.get("url") or "").strip()
        if not _allowed_audio_url(show_id, audio_url):
            continue
        guid = _text(item, "guid") or audio_url
        episode_id = hashlib.sha256(guid.encode("utf-8", "replace")).hexdigest()[:20]
        published, order = _published(_text(item, "pubDate"))
        description = _text(item, f"{ITUNES}summary") or _text(item, "description")
        duration = _duration(_text(item, f"{ITUNES}duration"))
        length = enclosure.get("length", "")
        try:
            size = max(0, int(length))
        except ValueError:
            size = 0
        episodes.append({
            "id": episode_id,
            "show_id": show_id,
            "show_title": config["title"],
            "title": _text(item, "title") or "Новый выпуск",
            "description": re.sub(r"<[^>]*>", " ", description)[:1400],
            "published": published,
            "published_order": order,
            "duration": duration,
            "size": size,
            "audio_url": f"/api/podcasts/audio/{show_id}/{episode_id}",
            "cover_url": _image(channel),
            "_source_url": audio_url,
        })
    episodes.sort(key=lambda ep: ep["published_order"], reverse=True)
    return {
        "id": show_id,
        "title": config["title"],
        "category": config["category"],
        "description": config["description"],
        "cover_url": _image(channel),
        "color": config["color"],
        "episodes": episodes,
    }


def _allowed_audio_url(show_id: str, url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.username is None
                and parts.password is None and parts.port in (None, 443)
                and (parts.hostname or "").lower() in FEEDS[show_id]["hosts"])
    except (KeyError, ValueError):
        return False


async def _fetch_feed(show_id: str) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=7),
                                 follow_redirects=False,
                                 headers={"User-Agent": "VibeMusicPodcasts/1.0"}) as client:
        response = await client.get(FEEDS[show_id]["feed"])
        response.raise_for_status()
        return _parse_feed(show_id, response.content)


async def _get_catalog() -> list[dict]:
    global _catalog_cache
    now = time.monotonic()
    if _catalog_cache and now - _catalog_cache[0] < _CACHE_TTL:
        return _catalog_cache[1]
    async with _catalog_lock:
        now = time.monotonic()
        if _catalog_cache and now - _catalog_cache[0] < _CACHE_TTL:
            return _catalog_cache[1]
        previous = {show["id"]: show for show in (_catalog_cache[1] if _catalog_cache else [])}
        async def load(show_id: str):
            try:
                return await _fetch_feed(show_id)
            except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
                if show_id in previous:
                    return previous[show_id]
                return {"id": show_id, "title": FEEDS[show_id]["title"],
                        "category": FEEDS[show_id]["category"],
                        "description": FEEDS[show_id]["description"],
                        "cover_url": "", "color": FEEDS[show_id]["color"],
                        "episodes": [], "error": "Лента временно недоступна"}
        shows = await asyncio.gather(*(load(show_id) for show_id in FEEDS))
        _catalog_cache = (time.monotonic(), shows)
        return shows


async def _find_episode(show_id: str, episode_id: str) -> tuple[dict, str]:
    if show_id not in FEEDS:
        raise HTTPException(404, "Подкаст не найден")
    shows = await _get_catalog()
    show = next((x for x in shows if x["id"] == show_id), None)
    episode = next((x for x in (show or {}).get("episodes", []) if x["id"] == episode_id), None)
    audio_url = (episode or {}).get("_source_url", "")
    if not episode or not _allowed_audio_url(show_id, audio_url):
        raise HTTPException(404, "Выпуск не найден или RSS временно недоступен")
    return episode, audio_url


@router.get("/catalog")
async def catalog():
    """Recent public RSS episodes from the curated podcast feeds."""
    shows = await _get_catalog()
    public_shows = []
    for show in shows:
        public_shows.append({**show, "episodes": [
            {key: value for key, value in episode.items() if not key.startswith("_")}
            for episode in show.get("episodes", [])
        ]})
    return {"shows": public_shows, "updated_at": datetime.now(timezone.utc).isoformat()}


@router.api_route("/audio/{show_id}/{episode_id}", methods=["GET", "HEAD"])
async def audio(show_id: str, episode_id: str, request: Request, download: bool = False):
    episode, audio_url = await _find_episode(show_id, episode_id)
    config = FEEDS[show_id]
    if download and episode.get("size", 0) > _MAX_AUDIO_BYTES:
        raise HTTPException(413, "Выпуск превышает лимит загрузки")
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=12, read=35),
                               follow_redirects=False,
                               headers={"User-Agent": "VibeMusicPodcasts/1.0"})
    upstream = None
    url = audio_url
    try:
        for _ in range(5):
            if not _allowed_audio_url(show_id, url):
                raise HTTPException(502, "Источник аудио не разрешён")
            headers = {}
            if request.headers.get("range"):
                headers["Range"] = request.headers["range"]
            upstream_request = client.build_request(request.method, url, headers=headers)
            upstream = await client.send(upstream_request, stream=True)
            if upstream.status_code in (301, 302, 303, 307, 308):
                location = upstream.headers.get("location", "")
                await upstream.aclose()
                url = urljoin(url, location)
                upstream = None
                continue
            break
        if upstream is None:
            raise HTTPException(502, "Слишком много перенаправлений источника")
        if upstream.status_code not in (200, 206):
            status = upstream.status_code
            await upstream.aclose()
            raise HTTPException(502, f"Источник аудио ответил {status}")
        length = upstream.headers.get("content-length")
        if length:
            try:
                too_large = int(length) > _MAX_AUDIO_BYTES
            except ValueError:
                too_large = False
            if too_large:
                await upstream.aclose()
                raise HTTPException(413, "Выпуск превышает лимит загрузки")
        if request.method == "HEAD":
            headers = {key: upstream.headers[key] for key in
                       ("content-length", "content-range", "accept-ranges") if key in upstream.headers}
            await upstream.aclose()
            await client.aclose()
            return StreamingResponse(iter(()), status_code=upstream.status_code,
                                     media_type="audio/mpeg", headers=headers)
        headers = {key: upstream.headers[key] for key in
                   ("content-length", "content-range", "accept-ranges", "last-modified", "etag")
                   if key in upstream.headers}
        headers["Cache-Control"] = "public, max-age=3600"
        safe_name = re.sub(r"[^\w .()-]", "_", episode["title"], flags=re.UNICODE).strip(" .")[:100] or "podcast"
        if download:
            headers["Content-Disposition"] = f"attachment; filename=\"{quote(safe_name)}.mp3\"; filename*=UTF-8''{quote(safe_name)}.mp3"
        async def body():
            try:
                async for chunk in upstream.aiter_raw(128 * 1024):
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(body(), status_code=upstream.status_code,
                                 media_type="audio/mpeg", headers=headers)
    except HTTPException:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise
    except httpx.HTTPError as exc:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, "Не удалось подключиться к аудиоисточнику") from exc
