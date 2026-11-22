"""Search and range-stream public-domain audiobooks from the official LibriVox API."""
from __future__ import annotations

import asyncio
import html
import re
import time
from urllib.parse import quote, urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/audiobooks", tags=["audiobooks"])
LIBRIVOX_API = "https://librivox.org/api/feed/audiobooks/"
_FIELDS = "id,title,description,language,authors,url_librivox,totaltime,totaltimesecs,coverart_jpg,coverart_thumbnail"
_TTL = 900
_CACHE_LIMIT = 120
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = asyncio.Lock()


def _cached(book_id: str) -> dict | None:
    entry = _cache.get(book_id)
    if not entry:
        return None
    if time.monotonic() - entry[0] >= _TTL:
        _cache.pop(book_id, None)
        return None
    return entry[1]


def _text(value: object, limit: int = 3000) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text)).strip()[:limit]


def _authors(rows: object) -> str:
    if not isinstance(rows, list):
        return "Неизвестный автор"
    names = [" ".join(filter(None, (str(row.get("first_name", "")).strip(),
                                      str(row.get("last_name", "")).strip())))
             for row in rows if isinstance(row, dict)]
    return ", ".join(name for name in names if name) or "Неизвестный автор"


def _trusted_archive_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return (parts.scheme == "https" and parts.username is None and parts.password is None
                and parts.port in (None, 443)
                and (host == "archive.org" or host.endswith(".archive.org")))
    except ValueError:
        return False


async def _upstream_book(book_id: str) -> dict:
    cached = _cached(book_id)
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8),
                                 follow_redirects=False,
                                 headers={"User-Agent": "VibeMusic-Audiobooks/1.0 (public catalog)"}) as client:
        response = await client.get(LIBRIVOX_API, params={
            "format": "json", "id": book_id, "limit": 1, "extended": 1,
            "coverart": 1, "fields": _FIELDS + ",sections",
        })
    if response.status_code == 404:
        raise HTTPException(404, "Книга не найдена в каталоге LibriVox")
    response.raise_for_status()
    books = response.json().get("books", [])
    book = next((row for row in books if str(row.get("id")) == book_id), None)
    if not isinstance(book, dict):
        raise HTTPException(404, "Книга не найдена в каталоге LibriVox")
    chapters = []
    for row in book.get("sections", []) if isinstance(book.get("sections"), list) else []:
        if not isinstance(row, dict) or not _trusted_archive_url(str(row.get("listen_url", ""))):
            continue
        section_id = str(row.get("id", ""))
        if not section_id.isdigit():
            continue
        try:
            duration = max(0, int(float(row.get("playtime", 0))))
        except (TypeError, ValueError, OverflowError):
            duration = 0
        readers = row.get("readers", [])
        narrator = ", ".join(str(r.get("display_name", "")).strip()
                              for r in readers if isinstance(r, dict) and r.get("display_name"))
        chapters.append({
            "id": section_id,
            "number": str(row.get("section_number", "")),
            "title": _text(row.get("title"), 240) or f"Глава {len(chapters) + 1}",
            "language": _text(row.get("language", book.get("language", "")), 60),
            "duration": duration,
            "narrator": _text(narrator, 240),
            "audio_url": f"/api/audiobooks/audio/{book_id}/{section_id}",
            "_source_url": str(row["listen_url"]),
        })
    normalized = {
        "id": book_id,
        "title": _text(book.get("title"), 500),
        "author": _authors(book.get("authors")),
        "language": _text(book.get("language"), 80),
        "description": _text(book.get("description"), 1800),
        "duration": max(0, int(book.get("totaltimesecs") or 0)),
        "cover_url": str(book.get("coverart_thumbnail") or book.get("coverart_jpg") or ""),
        "cover_large": str(book.get("coverart_jpg") or book.get("coverart_thumbnail") or ""),
        "catalog_url": str(book.get("url_librivox") or f"https://librivox.org/api/feed/audiobooks/?id={book_id}"),
        "source": "LibriVox · общественное достояние",
        "chapters": chapters,
    }
    async with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            oldest = min(_cache, key=lambda key: _cache[key][0])
            _cache.pop(oldest, None)
        _cache[book_id] = (time.monotonic(), normalized)
    return normalized


async def _fetch_page(params: dict) -> list[dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(22, connect=8),
                                 follow_redirects=False,
                                 headers={"User-Agent": "VibeMusic-Audiobooks/1.0 (public catalog)"}) as client:
        response = await client.get(LIBRIVOX_API, params=params)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    payload = response.json()
    return payload.get("books", []) if isinstance(payload, dict) else []


def _summary(row: dict) -> dict | None:
    book_id = str(row.get("id", ""))
    if not book_id.isdigit():
        return None
    return {
        "id": book_id,
        "title": _text(row.get("title"), 500),
        "author": _authors(row.get("authors")),
        "language": _text(row.get("language"), 80),
        "duration": max(0, int(row.get("totaltimesecs") or 0)),
        "cover_url": str(row.get("coverart_thumbnail") or row.get("coverart_jpg") or ""),
        "catalog_url": str(row.get("url_librivox") or f"https://librivox.org/api/feed/audiobooks/?id={book_id}"),
        "source": "LibriVox · общественное достояние",
    }


@router.get("/catalog")
async def catalog(q: str = Query("", max_length=120), offset: int = Query(0, ge=0, le=100000),
                  limit: int = Query(30, ge=1, le=60)):
    """Paginated discovery across LibriVox's 20k+ recordings, or title/author search."""
    params = {"format": "json", "limit": limit, "offset": offset,
              "sort_order": "desc", "coverart": 1, "fields": _FIELDS}
    query = q.strip()
    try:
        if query:
            if len(query) < 2:
                return {"books": [], "offset": 0, "limit": limit, "has_more": False,
                        "total_hint": "20 000+", "source": "LibriVox"}
            params.pop("offset")
            params["title"] = query
            title_rows = await _fetch_page(params)
            params.pop("title", None)
            params["author"] = query
            author_rows = await _fetch_page(params)
            merged = {str(row.get("id")): row for row in title_rows + author_rows
                      if isinstance(row, dict)}
            rows = list(merged.values())[:limit]
        else:
            rows = await _fetch_page(params)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Каталог LibriVox временно недоступен") from exc
    books = [item for row in rows if isinstance(row, dict) and (item := _summary(row))]
    return {"books": books, "offset": offset, "limit": limit,
            "has_more": len(rows) >= limit if not query else False,
            "total_hint": "20 000+", "source": "LibriVox"}


@router.get("/book/{book_id}")
async def book(book_id: str):
    if not book_id.isdigit():
        raise HTTPException(404, "Книга не найдена")
    try:
        result = await _upstream_book(book_id)
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "Не удалось загрузить главы книги") from exc
    return {key: value for key, value in result.items() if key != "chapters"} | {
        "chapters": [{key: value for key, value in chapter.items() if not key.startswith("_")}
                     for chapter in result["chapters"]]
    }


@router.api_route("/audio/{book_id}/{section_id}", methods=["GET", "HEAD"])
async def audio(book_id: str, section_id: str, request: Request, download: bool = False):
    if not book_id.isdigit() or not section_id.isdigit():
        raise HTTPException(404, "Глава не найдена")
    try:
        data = await _upstream_book(book_id)
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "Не удалось найти аудиофайл") from exc
    chapter = next((item for item in data["chapters"] if item["id"] == section_id), None)
    url = (chapter or {}).get("_source_url", "")
    if not url or not _trusted_archive_url(url):
        raise HTTPException(404, "Глава не найдена")
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=12, read=35),
                               follow_redirects=False,
                               headers={"User-Agent": "VibeMusic-Audiobooks/1.0"})
    upstream = None
    try:
        for _ in range(6):
            if not _trusted_archive_url(url):
                raise HTTPException(502, "Источник аудио не разрешён")
            headers = {}
            if request.headers.get("range"):
                headers["Range"] = request.headers["range"]
            upstream = await client.send(client.build_request(request.method, url, headers=headers),
                                         stream=True)
            if upstream.status_code in (301, 302, 303, 307, 308):
                location = upstream.headers.get("location", "")
                await upstream.aclose()
                upstream = None
                url = urljoin(url, location)
                continue
            break
        if upstream is None:
            raise HTTPException(502, "Слишком много перенаправлений источника")
        if upstream.status_code not in (200, 206):
            status = upstream.status_code
            await upstream.aclose()
            upstream = None
            raise HTTPException(502, f"Архив ответил {status}")
        length = upstream.headers.get("content-length")
        if download and length:
            try:
                if int(length) > 300 * 1024 * 1024:
                    await upstream.aclose()
                    upstream = None
                    raise HTTPException(413, "Файл главы слишком велик для загрузки")
            except ValueError:
                pass
        response_headers = {key: upstream.headers[key] for key in
                            ("content-length", "content-range", "accept-ranges", "last-modified", "etag")
                            if key in upstream.headers}
        response_headers["Cache-Control"] = "public, max-age=86400"
        if download:
            filename = re.sub(r"[^\w .()-]", "_", f"{data['title']} - {chapter['title']}",
                              flags=re.UNICODE).strip(" .")[:120] or "audiobook"
            response_headers["Content-Disposition"] = (
                f'attachment; filename="{quote(filename)}.mp3"; '
                f"filename*=UTF-8''{quote(filename)}.mp3")
        status = upstream.status_code
        media_type = upstream.headers.get("content-type", "audio/mpeg").split(";")[0]
        if request.method == "HEAD":
            await upstream.aclose()
            await client.aclose()
            return StreamingResponse(iter(()), status_code=status, media_type=media_type,
                                     headers=response_headers)

        async def body():
            try:
                async for chunk in upstream.aiter_raw(128 * 1024):
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(body(), status_code=status, media_type=media_type,
                                 headers=response_headers)
    except HTTPException:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise
    except httpx.HTTPError as exc:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, "Не удалось подключиться к Internet Archive") from exc
