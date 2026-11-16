"""YouTube Music: поиск через ytmusicapi (гостевой), fallback — yt-dlp (учитывает PROXY_URL).

Плейлисты читаются АНОНИМНО через innertube browse (WEB_REMIX) — открыто
даже для datacenter-IP (проверено 2026-11), без токенов и bot-check."""
import asyncio
import logging
from typing import Any

from services.providers.base import TrackMeta, parse_duration
from services.net import yt_dlp_search, cover_from_yt

log = logging.getLogger("providers.yt")

_YTI = "https://music.youtube.com/youtubei/v1/browse"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "Chrome/126.0 Safari/537.36")


def _walk(o: Any, key: str):
    """Рекурсивный сборщик значений по ключу в любом JSON от innertube."""
    if isinstance(o, dict):
        if key in o:
            yield o[key]
        for v in o.values():
            yield from _walk(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, key)


def _runs_text(textobj: dict) -> str:
    """simpleText | runs[] → единый текст."""
    if not isinstance(textobj, dict):
        return ""
    if textobj.get("simpleText"):
        return str(textobj["simpleText"])
    return "".join(str(r.get("text") or "")
                   for r in (textobj.get("runs") or []))

class YouTubeProvider:
    def __init__(self):
        self._ytm: Any = None
        self._ytm_dead: bool = False   # сетка YouTube недоступна → сразу yt-dlp

    def _client(self) -> Any:
        if self._ytm is None:
            from ytmusicapi import YTMusic
            self._ytm = YTMusic()  # гостевой режим, без логина
        return self._ytm

    async def search(self, query: str, limit: int = 8) -> list[TrackMeta]:
        from core.config import settings
        out: list[TrackMeta] = []
        if not self._ytm_dead:
            try:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(self._search_sync, query, limit),
                    timeout=settings.YT_PROBE_TIMEOUT)
                out = self._parse(raw) or []
            except (asyncio.TimeoutError, Exception) as e:
                # таймаут/ошибка → ytm недоступен, работаем через yt-dlp
                self._ytm_dead = True
                self._ytm = None
                out = []
                import logging
                logging.getLogger("providers.yt").info("ytmusicapi offline (%s) → yt-dlp", type(e).__name__)
        if not out:
            # fallback: yt-dlp гостевой поиск (работает там, где есть сеть/прокси)
            try:
                entries = await yt_dlp_search(query, limit=limit)
                out = [
                    TrackMeta(
                        source="ytmusic", external_id=e["id"],
                        title=e.get("title") or "",
                        artist=e.get("uploader") or e.get("channel") or "",
                        duration=int(e.get("duration") or 0),
                        cover_url=cover_from_yt(e))
                    for e in entries
                    if e.get("id") and not str(e.get("duration") or "").startswith("NA")
                ]
            except Exception:
                out = []
        return out[:limit]

    async def warmup(self) -> None:
        """Быстрая проверка сети при старте: если ytm недоступен → сразу yt-dlp."""
        try:
            await self.search("test", limit=1)
        except Exception:
            pass

    def _search_sync(self, query: str, limit: int) -> dict:
        return self._client().search(query, filter="songs", limit=limit)

    def _parse(self, raw: list[dict]) -> list[TrackMeta]:
        out: list[TrackMeta] = []
        for r in raw or []:
            vid = r.get("videoId")
            if not vid:
                continue
            artists = r.get("artists") or []
            artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            album = (r.get("album") or {}).get("name")
            dur = parse_duration(r.get("duration"))
            thumbs = r.get("thumbnails") or []
            cover = thumbs[-1]["url"] if thumbs else None
            out.append(TrackMeta(
                source="ytmusic", external_id=vid, title=r.get("title") or "",
                artist=artist, album=album, duration=dur, cover_url=cover))
        return out

    async def _innertube_playlist(self, playlist_id: str,
                                  limit: int | None = None) -> list[TrackMeta]:
        """Анонимный innertube-бридж: публичные плейлисты YTM/YouTube без токенов.

        WEB_REMIX-броум не проходит bot-check игрального API — и работает
        с серверных IP (проверено). Возвращает TrackMeta; пустой список — если
        плейлист приватный/недоступен.

        Пагинация (fix 16.11): первый browse отдаёт лишь ~100 первых позиций,
        остальное добираем по continuation-токенам тем же броком — иначе
        большие плейлисты приходили обрезанными до первой страницы."""
        from services.net import httpx_client

        ctx = {"context": {"client": {"clientName": "WEB_REMIX",
                                      "clientVersion": "1.20260101.01.00",
                                      "hl": "ru", "gl": "RU"}}}
        out: list[TrackMeta] = []
        seen: set[str] = set()

        def add(meta: TrackMeta) -> None:
            if meta.external_id and meta.external_id not in seen \
                    and meta.title:
                seen.add(meta.external_id)
                out.append(meta)

        def parse_items(j: dict) -> None:
            for pi in _walk(j, "playlistItemRenderer"):           # yt-стиль (RD-/PL-)
                vid = pi.get("videoId") or ""
                title = _runs_text(pi.get("title") or {})
                artist = _runs_text((pi.get("shortBylineText") or {})) or \
                    _runs_text((pi.get("byline") or {}))
                dur = parse_duration((pi.get("lengthText") or {}).get("simpleText"))
                thumbs = ((pi.get("thumbnail") or {}).get("thumbnails") or [])
                add(TrackMeta(source="ytmusic", external_id=vid, title=title,
                              artist=artist, duration=dur,
                              cover_url=thumbs[-1]["url"] if thumbs else None))

            for ri in _walk(j, "musicResponsiveListItemRenderer"):  # ytm-стиль
                vid = ((ri.get("playlistItemData") or {}).get("videoId")
                       or ri.get("videoId") or "")
                cols = ri.get("flexColumns") or []

                def col(i: int) -> str:
                    if i >= len(cols):
                        return ""
                    txt = ((cols[i].get("musicResponsiveListItemFlexColumnRenderer")
                            or {}).get("text") or {})
                    return _runs_text(txt)

                fixed = ri.get("fixedColumns") or []
                dur = ""
                if fixed:
                    ft = ((fixed[0].get("musicResponsiveListItemFixedColumnRenderer")
                           or {}).get("text") or {})
                    dur = _runs_text(ft)
                thumbs = (((ri.get("thumbnail") or {})
                           .get("musicThumbnailRenderer") or {})
                          .get("thumbnail") or {}).get("thumbnails") or []
                add(TrackMeta(source="ytmusic", external_id=vid, title=col(0),
                              artist=col(1), duration=parse_duration(dur),
                              cover_url=thumbs[-1]["url"] if thumbs else None))

        def cont_token(j: dict) -> str | None:
            for cr in _walk(j, "continuationItemRenderer"):
                tok = ((cr.get("continuationEndpoint") or {})
                       .get("continuationCommand") or {}).get("token")
                if tok:
                    return tok
                tok = ((cr.get("nextContinuationData") or {}).get("continuation"))
                if tok:
                    return str(tok)
            return None

        # прокси-aware клиент: на серверных IP innertube отдаёт обрывок (botwall),
        # через PROXY_URL — полный browse 200 (диагностика 347КБ против 836Б)
        async with httpx_client(timeout=35, follow_redirects=True,
                                trust_env=False) as c:
            token: str | None = None
            for page in range(40):          # 40 × ~100 позиций ≈ потолок 4000 треков
                body = dict(ctx)
                if token:
                    body["continuation"] = token
                else:
                    body["browseId"] = f"VL{playlist_id}"
                r = await c.post(_YTI, params={"prettyPrint": "false"},
                                 json=body, headers={"User-Agent": _UA})
                r.raise_for_status()
                j = r.json()
                if not token and (j.get("alertRenderer") or j.get("error")):
                    return []
                before = len(out)
                parse_items(j)
                token = cont_token(j)
                # страница не дала ничего нового либо продолжения нет — конец
                if len(out) == before or not token:
                    break
                if limit and len(out) >= limit:
                    break
        return out[:limit] if limit else out

    async def playlist(self, playlist_id: str, limit: int | None = None) -> list[TrackMeta]:
        """Публичный плейлист YouTube Music (id из ссылки).

        1) анонимный innertube-броум (нет bot-check, работает и без токенов);
        2) ytmusicapi гостевой;
        3) yt-dlp flat (последний резерв)."""
        out: list[TrackMeta] = []
        try:
            out = await self._innertube_playlist(playlist_id, limit)
        except Exception as e:
            log.info("innertube playlist %s: %s", playlist_id[:24],
                     type(e).__name__)
            out = []
        raw: list[dict] = []
        if not out and not self._ytm_dead:
            try:
                data = await asyncio.to_thread(
                    lambda: self._client().get_playlist(playlist_id, limit or 200))
                raw = data.get("tracks") or []
            except Exception:
                self._ytm_dead = True
                raw = []
        out = out or self._parse(raw)
        if not out:
            # fallback yt-dlp: flat playlist (работает и для youtube.com/playlist)
            try:
                entries = await _dlp_playlist(playlist_id)
                out = [
                    TrackMeta(
                        source="ytmusic", external_id=e["id"],
                        title=e.get("title") or "",
                        artist=e.get("uploader") or e.get("channel") or "",
                        duration=int(e.get("duration") or 0),
                        cover_url=cover_from_yt(e))
                    for e in entries if e.get("id")
                ][:limit or 200]
            except Exception:
                out = []
        return out

async def _dlp_playlist(playlist_id: str) -> list[dict]:
    import json as _json
    from services.net import yt_dlp_proxy_args, yt_dlp_binary
    cmd = [yt_dlp_binary(), "--flat-playlist", "--dump-json", "--no-warnings", "--quiet",
           *yt_dlp_proxy_args(), f"https://www.youtube.com/playlist?list={playlist_id}"]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    entries = []
    for line in out.decode(errors="ignore").splitlines():
        try:
            e = _json.loads(line)
        except Exception:
            continue
        if e.get("id") and e.get("title"):
            entries.append(e)
    return entries
