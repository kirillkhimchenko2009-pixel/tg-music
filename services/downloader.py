"""Скачивание треков: yt-dlp для YouTube/SoundCloud, прямые ссылки для ЯМ/Звук; конвертация ffmpeg; теги+обложка."""
import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from core.config import settings
from services.providers.base import TrackMeta
from services.net import yt_dlp_proxy_args, yt_dlp_binary

log = logging.getLogger("downloader")

@dataclass
class DownloadResult:
    path: Path
    quality: str
    size_bytes: int

_YT_URL = "https://music.youtube.com/watch?v={vid}"

def _report(on_progress, pct: float) -> None:
    """Прогресс-колбэк бывает и async (роут-джобы), и sync — шлём как умеем."""
    if not on_progress:
        return
    try:
        if asyncio.iscoroutinefunction(on_progress):
            asyncio.get_running_loop().create_task(on_progress(pct))
        else:
            on_progress(pct)
    except Exception:
        pass


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
                _report(on_progress, float(m.group(1)))
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"скачивание завершилось с кодом {proc.returncode}")

async def download_track(meta: TrackMeta, quality: str = "aac320",
                         file_name: str | None = None,
                         on_progress=None) -> DownloadResult | None:
    """Скачивает файл трека и возвращает путь. quality: 'aac320' | 'flac'."""
    settings.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    stem = file_name or f"{meta.source}_{re.sub(r'\W+', '_', meta.external_id)[:90]}"
    out_base = settings.AUDIO_DIR / stem
    log.info("download_track: source=%s ext_id=%.50s stem=%s",
             meta.source, meta.external_id, stem)

    yt_format = ("bestaudio[ext=m4a]/bestaudio" if quality == "aac320"
                 else "bestaudio/bestaudio")
    # soundcloud: external_id — готовый permalink_url (поиск отдаёт напрямую)
    page_url = (meta.external_id if meta.source == "soundcloud"
                else _YT_URL.format(vid=meta.external_id))
    cmd = [
        yt_dlp_binary(), "-f", yt_format,
        "-x", "--audio-format", "m4a", "--audio-quality", "320K",
        "--postprocessor-args", "ffmpeg: -b:a 320k",
        "--embed-metadata", "--embed-thumbnail",
        "--no-playlist", "--quiet", "--no-warnings",
        "--retries", "5", "--fragment-retries", "5", "--socket-timeout", "20",
        *yt_dlp_proxy_args(),
        "-o", str(out_base) + ".%(ext)s",
        page_url,
    ]
    await _run(cmd, on_progress)

    path = _find_outfile(out_base)
    if path is None:
        log.info("download_track %s: yt-dlp отработал, но файла нет "
                 "(base=%s)", meta.display(), out_base.name)
        return None
    log.info("download_track %s: ok %s (%s bytes)", meta.display(),
             path.name, path.stat().st_size)
    return DownloadResult(path=path, quality="aac320", size_bytes=path.stat().st_size)

async def download_ffmpeg_hls(playlist_url: str, meta: TrackMeta,
                              on_progress=None) -> "DownloadResult | None":
    """SoundCloud HLS (fMP4-плейлист) → mp3 на диск напрямую через ffmpeg.

    yt-dlp на датацентровых IP у SoundCloud регулярно спотыкается, а вот
    сам плейлист (m3u8, ссылка живёт 2–6 ч) ffmpeg отлично читает — этим и
    стрим-роут пользуется. Прогресс здесь грубый: 30 % на старте, 90 % после
    конвертации (длительность неизвестна заранее)."""
    import os
    import shutil

    if shutil.which("ffmpeg") is None:
        log.info("ffmpeg не найден — ffmpeg-hls недоступен")
        return None
    settings.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"\W+", "_", meta.external_id)[:90]
    out_path = settings.AUDIO_DIR / f"{meta.source}_{stem}.mp3"
    if out_path.exists() and out_path.stat().st_size > 96 * 1024:
        return DownloadResult(path=out_path, quality="aac320",
                              size_bytes=out_path.stat().st_size)

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "-protocol_whitelist", "http,https,tls,tcp,applehttps,httpproxy",
        "-multiple_requests", "1",
        "-i", playlist_url,
        "-vn", "-map", "0:a", "-c:a", "libmp3lame", "-b:a", "160k",
        "-y", out_path.as_posix(),
    ]
    env = dict(os.environ)
    if settings.PROXY_URL:
        env["http_proxy"] = env["https_proxy"] = settings.PROXY_URL
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE, env=env)
    _report(on_progress, 30.0)
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=420)
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
        log.info("ffmpeg-hls %s: таймаут 420 с", meta.display())
        return None
    if proc.returncode != 0:
        tail = (err or b"").decode(errors="replace").strip()[-300:]
        log.info("ffmpeg-hls %s: код %s, ffmpeg: %s",
                 meta.display(), proc.returncode, tail or "<без вывода>")
        out_path.unlink(missing_ok=True)
        return None
    size = out_path.stat().st_size
    if size < 96 * 1024:              # обрезка/ошибка — пустышку не отдаём
        log.info("ffmpeg-hls %s: подозрительно малый файл (%s байт)",
                 meta.display(), size)
        out_path.unlink(missing_ok=True)
        return None
    if on_progress:
        _report(on_progress, 90.0)
    log.info("ffmpeg-hls %s: ok (%s байт)", meta.display(), size)
    return DownloadResult(path=out_path, quality="aac320", size_bytes=size)


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
