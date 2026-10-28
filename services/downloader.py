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
    """Скачивание по прямой ссылке (ЯМ/Звук/zaycev/hitmoz), без перекодирования.
    use_proxy=False — для RU-сайтов, которые быстрее напрямую."""
    import httpx
    settings.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    ext = "flac" if quality == "flac" else \
          ("mp3" if url.lower().endswith(".mp3") else "m4a")
    out_path = settings.AUDIO_DIR / f"{meta.source}_{meta.external_id}.{ext}"
    proxy = (settings.PROXY_URL or None) if use_proxy else None
    async with httpx.AsyncClient(timeout=180, follow_redirects=True, proxy=proxy,
                                  headers=headers or {}) as c:
        async with c.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(out_path, "wb") as f:
                async for chunk in resp.aiter_bytes(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(min(done / total * 100, 100.0))
    return DownloadResult(path=out_path, quality=quality, size_bytes=out_path.stat().st_size)

def _find_outfile(base: Path) -> Path | None:
    base_dir = base.parent
    for p in base_dir.glob(base.name + ".*"):
        if p.suffix.lower() in (".m4a", ".mp3", ".opus", ".ogg", ".flac", ".webm"):
            return p
    return None
