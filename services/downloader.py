"""Скачивание треков: yt-dlp для YouTube, прямые ссылки для ЯМ/Звук; конвертация ffmpeg; теги+обложка."""
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from core.config import settings
from services.providers.base import TrackMeta
from services.net import yt_dlp_proxy_args, yt_dlp_binary

@dataclass
class DownloadResult:
    path: Path
    quality: str
    size_bytes: int

_YT_URL = "https://music.youtube.com/watch?v={vid}"

async def _run(cmd: list[str], on_progress=None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    pct_re = re.compile(r"(\d{1,3}(?:\.\d)?)%")
    assert proc.stdout
    async for raw in proc.stdout:
        line = raw.decode(errors="ignore")
        if on_progress:
            m = pct_re.search(line)
            if m:
                try:
                    on_progress(float(m.group(1)))
                except Exception:
                    pass
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"скачивание завершилось с кодом {proc.returncode}")

async def download_track(meta: TrackMeta, quality: str = "aac320",
                         file_name: str | None = None,
                         on_progress=None) -> DownloadResult | None:
    """Скачивает файл трека и возвращает путь. quality: 'aac320' | 'flac'."""
    settings.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    stem = file_name or f"{meta.source}_{meta.external_id}"
    out_base = settings.AUDIO_DIR / stem

    yt_format = ("bestaudio[ext=m4a]/bestaudio" if quality == "aac320"
                 else "bestaudio/bestaudio")
    cmd = [
        yt_dlp_binary(), "-f", yt_format,
        "-x", "--audio-format", "m4a", "--audio-quality", "320K",
        "--postprocessor-args", "ffmpeg: -b:a 320k",
        "--embed-metadata", "--embed-thumbnail",
        "--no-playlist", "--quiet", "--no-warnings",
        "--retries", "5", "--fragment-retries", "5", "--socket-timeout", "20",
        *yt_dlp_proxy_args(),
        "-o", str(out_base) + ".%(ext)s",
        _YT_URL.format(vid=meta.external_id),
    ]
    await _run(cmd, on_progress)

    path = _find_outfile(out_base)
    if path is None:
        return None
    return DownloadResult(path=path, quality="aac320", size_bytes=path.stat().st_size)

async def download_direct(url: str, meta: TrackMeta, quality: str,
                          on_progress=None, headers: dict | None = None,
                          use_proxy: bool = True) -> DownloadResult | None:
    """Скачивание по прямой ссылке (ЯМ/Звук/zaycev/hitmoz/hotplayer), без перекодирования.
    use_proxy=False — для RU-сайтов, которые быстрее напрямую.
    Расширение файла берём из content-type (у hotplayer ссылка без .mp3)."""
    import httpx
    settings.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    proxy = (settings.PROXY_URL or None) if use_proxy else None
    async with httpx.AsyncClient(timeout=180, follow_redirects=True, proxy=proxy,
                                  headers=headers or {}) as c:
        async with c.stream("GET", url) as resp:
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").lower()
            low = url.lower()
            if "flac" in ctype or (quality == "flac" and "mpeg" not in ctype):
                ext = "flac"
            elif "mpeg" in ctype or "mp3" in ctype or low.endswith(".mp3"):
                ext = "mp3"
            else:
                ext = "m4a"
            out_path = settings.AUDIO_DIR / f"{meta.source}_{meta.external_id}.{ext}"
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(out_path, "wb") as f:
                async for chunk in resp.aiter_bytes(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        rr = on_progress(min(done / total * 100, 100.0))
                        if asyncio.iscoroutine(rr):
                            await rr
    q = "flac" if ext == "flac" else quality
    return DownloadResult(path=out_path, quality=q, size_bytes=out_path.stat().st_size)

async def download_innertube(meta: TrackMeta, quality: str = "aac320",
                             on_progress=None) -> DownloadResult | None:
    """Прямое скачивание аудио YouTube по InnerTube-URL (без yt-dlp):
    переживает бот-чек там, где yt-dlp с датацентрового IP не проходит."""
    from services.providers import yt_stream
    got = await yt_stream.audio_url(meta.external_id)
    if not got:
        return None
    import httpx
    settings.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    ext = "m4a" if got["mime"] == "audio/mp4" else "webm"
    raw = settings.TEMP_DIR / f"yt_{meta.external_id}.{ext}"
    total = got.get("size") or 0
    done = 0
    async with httpx.AsyncClient(timeout=180, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as c:
        async with c.stream("GET", got["url"]) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or total or 0)
            with open(raw, "wb") as f:
                async for chunk in resp.aiter_bytes(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        rr = on_progress(min(done / total * 100, 100.0))
                    if asyncio.iscoroutine(rr):
                        await rr
    if not raw.exists() or raw.stat().st_size < 50_000:
        return None
    out_path = settings.AUDIO_DIR / f"{meta.source}_{meta.external_id}.m4a"
    if ext == "m4a":
        raw.replace(out_path)
    else:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(raw), "-vn", "-c:a", "aac", "-b:a", "320k",
            str(out_path), stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        raw.unlink(missing_ok=True)
        if proc.returncode != 0 or not out_path.exists():
            return None
    return DownloadResult(path=out_path, quality="aac320",
                          size_bytes=out_path.stat().st_size)


def _find_outfile(base: Path) -> Path | None:
    base_dir = base.parent
    for p in base_dir.glob(base.name + ".*"):
        if p.suffix.lower() in (".m4a", ".mp3", ".opus", ".ogg", ".flac", ".webm"):
            return p
    return None
