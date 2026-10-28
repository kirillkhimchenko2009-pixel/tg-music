"""Тегирование файлов: мета из каталога + обложка (mutagen)."""
import httpx
from pathlib import Path

from services.providers.base import TrackMeta

async def tag_file(path: Path, meta: TrackMeta) -> None:
    """Записывает ID3/mp4-теги и прикрепляет обложку, если ещё нет."""
    if not path.exists():
        return
    cover_path = None
    if meta.cover_url:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                r = await c.get(meta.cover_url)
                r.raise_for_status()
                cover_path = path.with_suffix(".cover.jpg")
                cover_path.write_bytes(r.content)
        except Exception:
            cover_path = None
    await _apply_tags(path, meta, cover_path)
    if cover_path and cover_path.exists():
        cover_path.unlink(missing_ok=True)

async def _apply_tags(path: Path, meta: TrackMeta, cover_path: Path | None) -> None:
    ext = path.suffix.lower()

    def sync():
        if ext in (".m4a", ".mp4"):
            return _tag_mp4(path, meta, cover_path)
        if ext in (".mp3",):
            return _tag_mp3(path, meta, cover_path)
        if ext == ".flac":
            return _tag_flac(path, meta, cover_path)
        return None
    import asyncio
    try:
        await asyncio.to_thread(sync)
    except Exception:
        pass

def _tag_mp4(path: Path, meta: TrackMeta, cover_path) -> None:
    from mutagen.mp4 import MP4, MP4Cover
    audio = MP4(path)
    audio["\xa9nam"] = [meta.title or ""]
    audio["\xa9ART"] = [(meta.artist or "")]
    if meta.album:
        audio["\xa9alb"] = [meta.album]
    if meta.year:
        audio["\xa9day"] = [str(meta.year)]
    if cover_path:
        audio["covr"] = [MP4Cover(cover_path.read_bytes(), imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()

def _tag_mp3(path: Path, meta: TrackMeta, cover_path) -> None:
    from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TYER
    tags = ID3(path)
    tags.delall("APIC")
    tags.add(TIT2(encoding=3, text=meta.title or ""))
    tags.add(TPE1(encoding=3, text=meta.artist or ""))
    if meta.album:
        tags.add(TALB(encoding=3, text=meta.album))
    if meta.year:
        tags.add(TYER(encoding=3, text=str(meta.year)))
    if cover_path:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3,
                      desc="Cover", data=cover_path.read_bytes()))
    tags.save(path)

def _tag_flac(path: Path, meta: TrackMeta, cover_path) -> None:
    from mutagen.flac import FLAC, Picture
    audio = FLAC(path)
    audio["title"] = meta.title or ""
    audio["artist"] = meta.artist or ""
    if meta.album:
        audio["album"] = meta.album
    if meta.year:
        audio["date"] = str(meta.year)
    if cover_path:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.data = cover_path.read_bytes()
        audio.clear_pictures()
        audio.add_picture(pic)
    audio.save()
