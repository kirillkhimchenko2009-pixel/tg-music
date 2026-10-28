"""ru.hitmoz.org: поисковая отдача с прямыми mp3-ссылками (анонимно).
Каждая строка выдачи содержит data-musmeta с artist/title/url — парсим регэкспом.
"""
import asyncio
import json
import logging
import re
from urllib.parse import urljoin

import httpx

from services import matching            # noqa: F401 (нужен для artist_guard)
from services.providers.base import TrackMeta, parse_duration

log = logging.getLogger("providers.hitmoz")
_BASE = "https://ru.hitmoz.org"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
       "Referer": _BASE + "/"}
_MUSMETA = re.compile(r"data-musmeta='([^']+)'")
_TIME = re.compile(r'track__time">.*?(\d{1,2}:\d{2})(?::\d{2})?', re.S)

# id → прямая mp3-ссылка (заполняется при каждом поиске)
_URLS: dict[str, str] = {}


def _safe_dur(s: str | None) -> int:
    try:
        return parse_duration(s)
    except Exception:
        return 0


def artist_guard(target: TrackMeta, cand: TrackMeta) -> bool:
    """Жёсткое условие по артисту: без него сайты типа hitmoz отдают
    тёзок-однофамильцев и бот присылает «не тот трек»."""
    if not target.artist:
        return True
    a = matching.norm(target.artist).split()
    b = matching.norm(cand.artist or "").split()
    if not b:
        return matching.similarity(target, cand) >= 0.7
    if set(a) & set(b):
        return True
    return any(len(x) >= 3 and (y.startswith(x) or x.startswith(y))
               for x in a for y in b)


def _variants(q: str) -> list[str]:
    """Варианты запроса: как есть → очищенный → только трек (без первого слова
    = артиста) → только артист. Позволяет обойти «дальновидный» поиск сайтов."""
    q0 = re.sub(r"\s+", " ", q or "").strip()
    out = [q0]
    parts = re.split(r"\s+-\s+|\s+—\s+|\s+", q0, maxsplit=1)
    if len(parts) == 2 and parts[1]:
        # сначала «только трек», потом «только артист»
        out.append(parts[1].strip())
        out.append(parts[0].strip())
    seen, uniq = set(), []
    for x in out:
        if x and x.lower() not in seen:
            seen.add(x.lower())
            uniq.append(x)
    return uniq[:4]


def _search_sync(query: str, limit: int) -> list[TrackMeta]:
    r = httpx.get(_BASE + "/search", params={"q": query}, headers=_UA, timeout=20,
                  follow_redirects=True)
    r.raise_for_status()
    out: list[TrackMeta] = []
    rest = r.text.split('<li class="tracks__item track')
    for block in rest[1:]:
        m = _MUSMETA.search(block)
        if not m:
            continue
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        url = urljoin(_BASE, d.get("url") or "")
        if not url:
            continue
        tid = str(d.get("id") or "").replace("track-id-", "") or url.rsplit("/", 1)[-1]
        t = _TIME.search(block)
        meta = TrackMeta(
            source="hitmoz", external_id=tid,
            title=d.get("title") or "", artist=d.get("artist") or "",
            duration=_safe_dur(t.group(1) if t else None),
            cover_url=d.get("img") or None)
        _URLS[tid] = url
        out.append(meta)
        if len(out) >= limit:
            break
    return out


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    """Пробует варианты запроса, склеивая уникальные кандидаты."""
    pool: list[TrackMeta] = []
    seen: set[str] = set()
    try:
        for v in _variants(query):
            got = await asyncio.to_thread(_search_sync, v, limit)
            for c in got:
                if c.external_id in seen:
                    continue
                seen.add(c.external_id)
                pool.append(c)
            # достаточно кандидатов — не дёргаем сайт дальше
            if len(pool) >= limit:
                break
    except Exception as e:
        log.info("hitmoz поиск не удался (%s)", type(e).__name__)
        return pool[:limit]
    # короткая пауза между запросами к сайту
    await asyncio.sleep(0.15)
    return pool[:limit]


def _download_url_sync(external_id: str, artist: str, title: str) -> str | None:
    url = _URLS.get(external_id)
    if not url:
        # после рестарта бота кеш пуст — перезапрашиваем выдачу
        try:
            _search_sync(f"{artist} {title}".strip(), 12)
        except Exception:
            pass
        url = _URLS.get(external_id)
    return url


async def download_url(meta: TrackMeta) -> str | None:
    return await asyncio.to_thread(_download_url_sync, meta.external_id,
                                   meta.artist, meta.title)
