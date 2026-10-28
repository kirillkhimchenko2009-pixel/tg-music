"""YouTube Music: поиск через ytmusicapi (гостевой), fallback — yt-dlp (учитывает PROXY_URL)."""
import asyncio
from typing import Any

from services.providers.base import TrackMeta, parse_duration
from services.net import yt_dlp_search, cover_from_yt

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

    async def playlist(self, playlist_id: str, limit: int | None = None) -> list[TrackMeta]:
        """Публичный плейлист YouTube Music (id из ссылки)."""
        raw: list[dict] = []
        if not self._ytm_dead:
            try:
                data = await asyncio.to_thread(
                    lambda: self._client().get_playlist(playlist_id, limit or 200))
                raw = data.get("tracks") or []
            except Exception:
                self._ytm_dead = True
                raw = []
        out = self._parse(raw)
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
