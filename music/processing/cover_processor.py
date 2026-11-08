"""Cover Integrity: поиск максимального оригинала, проверка, миниатюры."""
from __future__ import annotations

import io
import logging
import re

from PIL import Image

from ..config import music_settings
from ..domain import CoverAnalysis, CoverCandidate, TrackCandidate
from ..net import SafeHttpClient
from ..quality.cover_checker import analyze_image, choose_best
from ..quality.metadata_checker import normalize_tags
from .metadata_editor import embedded_cover

log = logging.getLogger("music.processing.cover")


def upgrade_cover_url(url: str) -> str:
    """Пытается получить оригинал максимального разрешения (без апскейла)."""
    if not url:
        return url
    upgraded = url
    if "sndcdn.com" in upgraded:
        upgraded = re.sub(r"-(?:large|t\d+x\d+|crop|badge|mini)\.",
                          "-t500x500.", upgraded)
    # обложки Jamendo отдаются и с jamendo.com, и с CDN imgjam.com
    if "jamendo.com" in upgraded or "imgjam.com" in upgraded:
        upgraded = re.sub(r"([?&])size=\d+", r"\g<1>size=1200", upgraded)
        if "size=" not in upgraded:
            upgraded += ("&" if "?" in upgraded else "?") + "size=1200"
    if "bcbits.com" in upgraded:
        upgraded = re.sub(r"_\d+\.(jpg|jpeg|png|webp)$", r"_10.\1", upgraded, flags=re.I)
    return upgraded


def _candidate_urls(track: TrackCandidate, extra_url: str | None = None) -> list[str]:
    urls: list[str] = []
    for raw in (extra_url, track.cover_url):
        if not raw:
            continue
        up = upgrade_cover_url(raw)
        for value in (up, raw):
            if value and value not in urls:
                urls.append(value)
    return urls[:4]


async def fetch_cover(track: TrackCandidate, provider, *,
                      max_mb: int | None = None) -> CoverAnalysis | None:
    urls = _candidate_urls(track)
    if not urls:
        return None
    limit = (max_mb or music_settings.max_cover_mb) * 1024 * 1024
    best = None
    async with provider.http() as client:
        for url in urls:
            try:
                result = await client.request(
                    "GET", url, expected_types=("image/*", "application/octet-stream"),
                    max_bytes=limit)
            except Exception as exc:  # noqa: BLE001
                log.debug("cover %s: %s", url, exc)
                continue
            analysis = analyze_image(result.data, source_url=url, source=url)
            if analysis.candidate.is_valid:
                if best is None or analysis.score > best.score:
                    best = analysis
                if analysis.accepted and analysis.candidate.pixels >= 500 * 500:
                    break
    return best


async def prepare_cover(track: TrackCandidate, provider, *,
                        local_path: str | None = None) -> CoverAnalysis | None:
    """Сравнивает встроенную и сетевые обложки, возвращает лучшую."""
    analyses: list[CoverAnalysis] = []
    if local_path:
        embedded = embedded_cover(local_path)
        if embedded:
            data, _mime = embedded
            analysis = analyze_image(data, source_url=str(local_path), source="embedded")
            if analysis.candidate.is_valid:
                analyses.append(analysis)
    fetched = await fetch_cover(track, provider)
    if fetched and fetched.candidate.is_valid:
        analyses.append(fetched)
    return choose_best(analyses)


def make_thumbnail(data: bytes, *, size: int = 320, quality: int = 82) -> bytes | None:
    """Только для Telegram thumbnail: квадратный JPEG, основная обложка не меняется."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            rgb = image.convert("RGB")
            w, h = rgb.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            rgb = rgb.crop((left, top, left + side, top + side))
            rgb.thumbnail((size, size), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            rgb.save(out, format="JPEG", quality=quality, optimize=True)
            result = out.getvalue()
            if len(result) > 200 * 1024:
                out = io.BytesIO()
                rgb.save(out, format="JPEG", quality=65, optimize=True)
                result = out.getvalue()
            return result if len(result) <= 200 * 1024 else None
    except Exception as exc:  # noqa: BLE001
        log.debug("thumbnail: %s", exc)
        return None


def make_background(data: bytes, *, size: int = 640, blur: int = 18) -> bytes | None:
    """Отдельная размытая копия для фона интерфейса. Основная обложка не трогается."""
    try:
        from PIL import ImageFilter
        with Image.open(io.BytesIO(data)) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((size, size), Image.Resampling.LANCZOS)
            rgb = rgb.filter(ImageFilter.GaussianBlur(blur))
            out = io.BytesIO()
            rgb.save(out, format="JPEG", quality=80, optimize=True)
            return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def best_cover_bytes(track: TrackCandidate, analysis: CoverAnalysis | None) -> tuple[bytes, str]:
    if analysis and analysis.candidate.is_valid:
        return analysis.candidate.data, analysis.candidate.mime
    return b"", ""


def store_cover_copy(data: bytes, mime: str, key: str) -> str | None:
    """Сохраняет оригинальный байт-в-байт файл обложки (без resize/апскейла)."""
    from ..config import music_settings
    ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(
        mime, ".jpg")
    try:
        folder = music_settings.covers_dir / (key or "unknown")[:2]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{(key or 'cover')[:40]}{ext}"
        if not path.exists():
            path.write_bytes(data)
        return str(path)
    except OSError as exc:
        log.debug("store_cover_copy: %s", exc)
        return None


__all__ = ["upgrade_cover_url", "fetch_cover", "prepare_cover", "make_thumbnail",
           "make_background", "best_cover_bytes", "store_cover_copy",
           "embedded_cover", "normalize_tags"]
