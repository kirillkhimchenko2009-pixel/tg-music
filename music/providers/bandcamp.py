"""Bandcamp: без публичного API — только прямая ссылка и официальная страница.

Мы не обходим покупку и не вытаскиваем защищённые файлы: адаптер отдаёт
метаданные страницы и статус PURCHASE_REQUIRED / OFFICIAL_LINK_ONLY.
"""
from __future__ import annotations

import html
import json
import logging
import re
from urllib.parse import quote, urlsplit

from ..domain import AudioSource, PermissionInfo, ProviderCapabilities, TrackCandidate
from ..enums import PermissionStatus, ProviderHealth, TrackVersion
from ..errors import NotSupportedError, ProviderError
from .base import MusicProvider

log = logging.getLogger("music.provider.bandcamp")

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    re.I | re.S)
_META_RE = re.compile(r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
                      re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_ISO_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", re.I)


def extract_bandcamp_url(text: str) -> str:
    for match in _URL_RE.findall(text or ""):
        host = (urlsplit(match).hostname or "").lower()
        if host == "bandcamp.com" or host.endswith(".bandcamp.com"):
            return match.rstrip(").,;\"'")
    return ""


class BandcampProvider(MusicProvider):
    name = "bandcamp"
    display_name = "Bandcamp"
    priority = 50
    capabilities = ProviderCapabilities(search=False, direct_link=True,
                                        metadata=True, cover=True,
                                        download=False, stream=False,
                                        requires_auth=False,
                                        notes="покупка/официальная страница")
    # страницы — на bandcamp.com, обложки — на CDN bcbits.com
    api_hosts = ("bandcamp.com", "bcbits.com")

    @property
    def enabled(self) -> bool:
        return self.settings.bandcamp_enabled

    def status(self) -> ProviderHealth:
        return ProviderHealth.OK if self.enabled else ProviderHealth.DISABLED

    async def search(self, query: str, limit: int = 8) -> list[TrackCandidate]:
        """Поиск поддерживает только прямую ссылку (официального API нет)."""
        url = extract_bandcamp_url(query)
        if not url:
            return []
        item = await self._fetch(url)
        return [item] if item else []

    async def get_track(self, external_id: str) -> TrackCandidate | None:
        url = extract_bandcamp_url(external_id) or (
            external_id if extract_bandcamp_url(f"https://{external_id}") else "")
        if not url:
            return None
        return await self._fetch(url)

    async def _fetch(self, url: str) -> TrackCandidate | None:
        if not self.enabled:
            return None
        try:
            async with self.http() as client:
                res = await client.request("GET", url,
                                           expected_types=("text/html", "application/xhtml+xml"),
                                           max_bytes=3 * 1024 * 1024)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Bandcamp: страница недоступна: {exc}",
                                code="BANDCAMP_FETCH") from exc
        page = res.data.decode("utf-8", errors="replace")
        meta = self._ld_json(page) or {}
        og = {k.lower(): html.unescape(v) for k, v in _META_RE.findall(page)}
        title = str(meta.get("name") or og.get("og:title") or "").strip()
        if not title:
            m = _TITLE_RE.search(page)
            title = html.unescape(m.group(1)).strip() if m else ""
        title = re.sub(r"\s*[|—]\s*Bandcamp\s*$", "", title, flags=re.I)
        if not title:
            return None
        artist = ""
        by = meta.get("byArtist") or meta.get("artist")
        if isinstance(by, dict):
            artist = str(by.get("name") or "")
        elif isinstance(by, list) and by and isinstance(by[0], dict):
            artist = str(by[0].get("name") or "")
        elif isinstance(by, str):
            artist = by
        album = ""
        in_album = meta.get("inAlbum") or meta.get("album")
        if isinstance(in_album, dict):
            album = str(in_album.get("name") or "")
        image = str(meta.get("image") or og.get("og:image") or "")
        duration = self._iso_duration(str(meta.get("duration") or ""))
        date = str(meta.get("datePublished") or og.get("music:release_date") or "")
        year = int(date[:4]) if date[:4].isdigit() else None
        offers = meta.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        try:
            price = float(offers.get("price")) if offers.get("price") is not None else None
        except (TypeError, ValueError):
            price = None
        if price is not None and price <= 0:
            permission = PermissionStatus.OFFICIAL_LINK_ONLY
            reason = "Bandcamp: бесплатная/добровольная оплата, только страница источника"
        else:
            permission = PermissionStatus.PURCHASE_REQUIRED
            reason = "Bandcamp: требуется покупка на официальной странице"
        return self.make_candidate(
            external_id=url,
            title=title,
            artist=artist,
            album=album,
            duration=duration,
            year=year,
            cover_url=image or None,
            source_url=url,
            permission=permission,
            license_name="Bandcamp",
            license_url=url,
            version=self._version(title),
            explicit=False,
            file_format=None,
            downloadable=False,
            streamable=False,
            extra={"price": price, "reason": reason,
                   "og_description": og.get("og:description", "")[:500]},
        )

    @staticmethod
    def _ld_json(page: str) -> dict:
        for raw in _LD_RE.findall(page):
            try:
                data = json.loads(raw.strip())
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and "@graph" in data:
                items = data.get("@graph") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("@type") or "")
                if kind in ("MusicRecording", "MusicAlbum", "AudioObject", "Product"):
                    return item
        return {}

    @staticmethod
    def _iso_duration(value: str) -> int:
        m = _ISO_DUR_RE.search(value or "")
        if not m:
            return 0
        hours, minutes, seconds = m.groups()
        total = int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(float(seconds or 0))
        return total

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        status = (track.permission if track.permission in
                  (PermissionStatus.PURCHASE_REQUIRED, PermissionStatus.OFFICIAL_LINK_ONLY)
                  else PermissionStatus.PURCHASE_REQUIRED)
        return PermissionInfo(
            status=status,
            reason=(track.extra or {}).get("reason")
            or "Bandcamp: доступна только официальная страница",
            source_url=self.get_source_url(track),
            license_name="Bandcamp", license_url=track.source_url,
            purchase_url=track.source_url)

    async def get_audio_stream(self, track: TrackCandidate) -> AudioSource:
        raise NotSupportedError(
            "Bandcamp: скачивание доступно только после покупки на официальной странице",
            code="BANDCAMP_PURCHASE")

    async def health_check(self) -> ProviderHealth:
        if not self.enabled:
            return ProviderHealth.DISABLED
        try:
            async with self.http() as client:
                await client.request("GET", "https://bandcamp.com/",
                                     expected_types=("text/html",),
                                     max_bytes=512 * 1024)
            return ProviderHealth.OK
        except Exception as exc:  # noqa: BLE001
            log.info("bandcamp health: %s", exc)
            return ProviderHealth.DOWN

    @staticmethod
    def _version(title: str) -> TrackVersion:
        low = title.lower()
        if "remix" in low:
            return TrackVersion.REMIX
        if "live" in low:
            return TrackVersion.LIVE
        if "acoustic" in low:
            return TrackVersion.ACOUSTIC
        if "instrumental" in low:
            return TrackVersion.INSTRUMENTAL
        return TrackVersion.ORIGINAL
