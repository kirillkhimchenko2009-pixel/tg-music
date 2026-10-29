"""REST API для Mini App: поиск, каталог, загрузки, стримы."""
import asyncio
import base64
import hashlib
import hmac
import json as _json
import time as _time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select

from bot import runtime
from core.config import settings
from core.models import Track
from server.auth import CurrentUser, tg_user
from services import catalog
from services.providers.youtube import YouTubeProvider

router = APIRouter(prefix="/api", tags=["tracks"])
yt = YouTubeProvider()
JOB_STATUS: dict[str, dict] = {}


# ---------------- короткоживущие playback-токены ----------------
# <audio src=...> не умеет заголовки — отдаём подписанную ссылку /api/stream/{id}?t=...
_KEY = hashlib.sha256(("streamv2|" + settings.BOT_TOKEN).encode()).digest()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_stream_token(uid: int, track_id: int, ttl: int = 6 * 3600) -> str:
    payload = _b64u(_json.dumps({"u": uid, "tr": track_id,
                                 "e": int(_time.time()) + ttl}).encode())
    sig = _b64u(hmac.new(_KEY, payload.encode(), hashlib.sha256).digest())
    return payload + "." + sig


def check_stream_token(t: str):
    try:
        payload, sig = t.split(".", 1)
        ok = hmac.compare_digest(sig, _b64u(hmac.new(_KEY, payload.encode(), hashlib.sha256).digest()))
        if not ok:
            return None
        d = _json.loads(_b64u_dec(payload))
        if int(d.get("e", 0)) < _time.time():
            return None
        return int(d.get("u", 0))
    except Exception:
        return None

def track_dict(t: Track) -> dict:
    return {
        "id": t.id, "title": t.title, "artist": t.artist, "album": t.album,
        "year": t.year, "duration": t.duration, "cover_url": t.cover_url,
        "quality": t.quality, "ready": bool(t.file_id),
        "downloads": t.downloads,
    }

@router.get("/search")
async def search(q: str = "", limit: int = 24, user: CurrentUser = Depends(tg_user)):
    """Поиск: сначала локальный каталог, затем внешние источники (ЯМ→Звук по токенам, YT)."""
    q = (q or "").strip()
    if not q:
        return {"local": [], "web": []}
    from services.authstore import get_token
    from services.providers import zvuk as zv
    from services.providers.yandex import YandexProvider

    web: list = []
    uid = int(user.id)

    def merge(metas=None, arr_pair=None):
        for m in (metas or []):
            if not any(x.source == m.source and x.external_id == m.external_id for x in web):
                web.append(m)

    async with runtime.get_session() as s:
        local = await catalog.local_search(s, q, limit=limit)
        zt = await get_token(s, uid, "zvuk")
        ymt = await get_token(s, uid, "ym")

    if ymt:
        try:
            merge(await YandexProvider(ymt).search(q, limit=limit))
        except Exception:
            pass
    if zt:
        try:
            merge(await zv.search(q, limit=limit, token=zt))
        except Exception:
            pass
    try:
        merge(await yt.search(q, limit=limit))
    except Exception:
        pass

    out = []
    async with runtime.get_session() as s:
        for m in web:
            t = await catalog.upsert_track(s, m)
            await s.commit()
            out.append(track_dict(t))
    return {"local": [track_dict(t) for t in local],
            "web": out}

@router.get("/library")
async def library(limit: int = 100, offset: int = 0,
                  user: CurrentUser = Depends(tg_user)):
    """Треки, когда-либо скачивавшиеся (есть file_id)."""
    async with runtime.get_session() as s:
        q = (select(Track).where(Track.file_id.isnot(None), Track.file_id != "")
             .order_by(Track.downloads.desc(), Track.id.desc())
             .limit(min(limit, 500)).offset(offset))
        rows = list((await s.execute(q)).scalars())
    return {"tracks": [track_dict(t) for t in rows]}

@router.get("/tracks/{track_id}")
async def get_track(track_id: int, user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        t = await catalog.get_track(s, track_id)
        if not t:
            raise HTTPException(404, "нет такого трека")
        return track_dict(t)

@router.post("/download")
async def download(track_id: int, quality: str = "",
                   user: CurrentUser = Depends(tg_user)):
    """Фоновая доставка трека в чат пользователя (с шагом прогресса)."""
    quality = quality or settings.DEFAULT_QUALITY
    job_id = uuid.uuid4().hex[:12]
    JOB_STATUS[job_id] = {"status": "queued", "progress": 0.0, "user": user.id}
    asyncio.create_task(_deliver_job(job_id, int(user.id), track_id, quality))
    return {"job": job_id}

_BOT = None

async def _bot():
    global _BOT
    if _BOT is None:
        from bot.main import create_bot
        _BOT = create_bot()
    return _BOT

async def _deliver_job(job_id: str, user_id: int, track_id: int, quality: str):
    from services import catalog, ingest
    st = JOB_STATUS[job_id]
    bot = await _bot()
    try:
        from services.catalog import meta_of
        async with runtime.get_session() as s:
            t = await catalog.get_track(s, track_id)
            if not t:
                st.update(status="error", error="нет такого трека")
                return

            async def on_progress(p):
                if p - st.get("progress", 0.0) >= 2.0 or p >= 100:
                    st.update(progress=p, status="downloading")

            res = await ingest.resolve(s, meta_of(t), quality, user_id=user_id,
                                       on_progress=on_progress)
            if res.status == "not_found" or not (res.file_path or res.track):
                st.update(status="error", error=res.error or "не удалось скачать")
                return
            target = res.track or (await catalog.get_track(s, track_id))
            if res.file_path and target and res.file_path.exists():
                from bot.runtime import send_audio_file
                fid = await send_audio_file(bot, user_id, target, res.file_path)
                st.update(status="done", progress=100.0,
                          telegram=fid is not None)
            elif target and target.file_id:
                from bot.runtime import send_track_cached
                st.update(status="done", progress=100.0,
                          telegram=True if await send_track_cached(bot, user_id, target) else False)
            else:
                st.update(status="error", error="файл не доступен")
    except Exception as e:  # noqa: BLE001
        st.update(status="error", error=str(e)[:200])

@router.get("/jobs/{job_id}")
async def job_status(job_id: str, user: CurrentUser = Depends(tg_user)):
    status = JOB_STATUS.get(job_id)
    if not status:
        raise HTTPException(404, "нет такой задачи")
    return status

# ---------------- потоковый плеер ----------------
_FILE_PATH_CACHE: dict[int, tuple[str, float]] = {}    # track_id -> (tg file_path, expire)
_AUDIO_URL_CACHE: dict[int, tuple[str, float]] = {}    # track_id -> (direct audio url, expire)


async def stream_user(
        request: Request,
        t: str = "",
        x_init_data: str = Header(default=""),
        authorization: str = Header(default="")):
    """Для <audio>: либо подписанный токен в query, либо обычные заголовки."""
    if t:
        uid = check_stream_token(t)
        if uid is not None:
            return CurrentUser(id=uid, username=None, first_name="player")
    return await tg_user(init_data=x_init_data, authorization=authorization)


@router.get("/player/{track_id}")
async def player_src(track_id: int, user: CurrentUser = Depends(tg_user)):
    """Лёгкий JSON со ссылкой на поток (нативное воспроизведение, без blob)."""
    async with runtime.get_session() as s:
        t = await catalog.get_track(s, track_id)
        if not t:
            raise HTTPException(404, "нет такого трека")
    return {"url": f"/api/stream/{track_id}?t={make_stream_token(int(user.id), track_id)}"}


async def _tg_file_path(track_id: int, file_id: str) -> str | None:
    """file_path из Telegram с коротким кэшем (не дёргаем get_file на каждом Range)."""
    hit = _FILE_PATH_CACHE.get(track_id)
    if hit and hit[1] > _time.time():
        return hit[0]
    from bot.main import create_bot
    bot = create_bot()
    try:
        f = await bot.get_file(file_id)
        fp = f.file_path
    finally:
        await bot.session.close()
    if fp:
        _FILE_PATH_CACHE[track_id] = (fp, _time.time() + 45 * 60)
    return fp


async def _resolve_online_audio(track_id: int, t: Track) -> str | None:
    """Прямой аудио-URL для нескачанных треков: быстрые 30-сек превью
    (Deezer → iTunes, ~1 с) — мгновенный старт; полный звук YouTube —
    только если превью не нашлось (с зарубежных IP YT часто блокирует)."""
    hit = _AUDIO_URL_CACHE.get(track_id)
    if hit and hit[1] > _time.time():
        return hit[0]
    dz, it = await asyncio.gather(_deezer_preview(t), _itunes_preview(t),
                                  return_exceptions=True)
    url = (dz if isinstance(dz, str) else None) or (it if isinstance(it, str) else None)
    if not url:
        vid = t.external_id if (t.source or "") == "ytmusic" else ""
        url = await _yt_audio_url(vid=vid, query=f"{t.artist or ''} {t.title or ''}")
    if url:
        _AUDIO_URL_CACHE[track_id] = (url, _time.time() + 35 * 60)
    return url


async def _deezer_preview(t: Track) -> str | None:
    """Анонимное превью (30 сек) с Deezer — работает без токенов и без VPN."""
    import httpx

    def _sync() -> str | None:
        from services.matching import best_match
        from services.providers.base import TrackMeta as TM
        q = f"{t.artist or ''} {t.title or ''}".strip()
        if not q:
            return None
        r = httpx.get("https://api.deezer.com/search",
                      params={"q": q, "limit": 8}, timeout=12)
        data = r.json().get("data") or []
        cands, previews = [], {}
        for d in data:
            tid = str(d.get("id") or "")
            cands.append(TM(source="deezer", external_id=tid,
                            title=d.get("title") or "",
                            artist=(d.get("artist") or {}).get("name") or "",
                            duration=int(d.get("duration") or 0)))
            previews[tid] = d.get("preview")
        m = best_match(TM(source="deezer", external_id="", title=t.title or "",
                          artist=t.artist or ""), cands, min_score=0.5)
        if m:
            return previews.get(m.external_id)
        # матчинг не сработал (транслит/фиты) — доверяем релевантности Deezer
        return (data[0].get("preview") if data else None)
    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=20)
    except Exception:
        return None


async def _itunes_preview(t: Track) -> str | None:
    """30-сек превью из iTunes Search API (CDN Apple, без токенов)."""
    import httpx

    def _sync() -> str | None:
        from services.matching import best_match
        from services.providers.base import TrackMeta as TM
        q = f"{t.artist or ''} {t.title or ''}".strip()
        if not q:
            return None
        r = httpx.get("https://itunes.apple.com/search",
                      params={"term": q, "media": "music", "entity": "song",
                              "limit": 8}, timeout=12)
        data = r.json().get("results") or []
        cands, previews = [], {}
        for d in data:
            tid = str(d.get("trackId") or "")
            cands.append(TM(source="itunes", external_id=tid,
                            title=d.get("trackName") or "",
                            artist=d.get("artistName") or "",
                            duration=int((d.get("trackTimeMillis") or 0) / 1000)))
            previews[tid] = d.get("previewUrl")
        m = best_match(TM(source="itunes", external_id="", title=t.title or "",
                          artist=t.artist or ""), cands, min_score=0.5)
        if m:
            return previews.get(m.external_id)
        return (data[0].get("previewUrl") if data else None)
    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=15)
    except Exception:
        return None


async def _yt_audio_url(vid: str, query: str = "") -> str | None:
    """Аудио-URL конкретного видео (или первого результата поиска):
    InnerTube (быстро, без yt-dlp) → yt-dlp (с прокси при необходимости)."""
    if vid:
        try:
            from services.providers import yt_stream
            got = await yt_stream.audio_url(vid)
            if got:
                return got["url"]
        except Exception:
            pass
    from services.net import yt_dlp_binary, yt_dlp_proxy_args
    target = f"https://www.youtube.com/watch?v={vid}" if vid else f"ytsearch1:{query}"
    cmd = [yt_dlp_binary(), "-f", "bestaudio/best", "--no-playlist", "--no-warnings",
           "--quiet", "--dump-json", *yt_dlp_proxy_args(), target]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=70)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        if proc.returncode != 0:
            return None
        d = _json.loads(out.decode(errors="ignore").splitlines()[0])
    except Exception:
        return None

    def direct(f: dict) -> bool:
        proto = (f.get("protocol") or "")
        u = (f.get("url") or "").lower()
        return bool(u) and u.startswith("http") and "m3u8" not in proto and "mpd" not in proto

    best = d.get("url")
    if not direct({"url": best, "protocol": d.get("protocol")}):
        cands = [f for f in (d.get("formats") or []) if direct(f)]
        ac = [f for f in cands if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
        pool = ac or [f for f in cands if f.get("vcodec") in (None, "none")] or cands
        if pool:
            pool.sort(key=lambda f: (f.get("abr") or 0) or (f.get("tbr") or 0), reverse=True)
            best = pool[0]["url"]
    return best or None


@router.get("/stream/{track_id}")
async def stream(track_id: int,
                 request: Request,
                 t: str = "",
                 user: CurrentUser = Depends(stream_user)):
    """Нативный поток для плеера: локальный файл → Telegram CDN (Range-проброс)
    → онлайн-фолбэк YouTube для нескачанных треков."""
    rng = request.headers.get("range")

    async with runtime.get_session() as s:
        tr = await catalog.get_track(s, track_id)
        if not tr:
            raise HTTPException(404, "нет такого трека")

        # 1) локально лежащий файл
        if tr.local_path:
            from pathlib import Path
            p = Path(tr.local_path)
            if p.exists():
                total = p.stat().st_size
                if rng:
                    a, b = (rng.split("bytes=")[-1].split("-") + [None])[:2]
                    start = int(a or 0)
                    end = min(int(b), total - 1) if b else total - 1
                    if end < start or start >= total:
                        from fastapi.responses import Response
                        return Response(content=b"", status_code=416, headers={
                            "Content-Range": f"bytes */{total}"})
                    return StreamingResponse(_file_slice_gen(p, start, end),
                                             status_code=206, media_type="audio/mpeg",
                                             headers={"Accept-Ranges": "bytes",
                                                      "Content-Range": f"bytes {start}-{end}/{total}",
                                                      "Content-Length": str(end - start + 1)})
                return FileResponse(p, media_type="audio/mpeg",
                                    headers={"Accept-Ranges": "bytes"})

        # 2) файл в Telegram CDN — реле с зеркалом Content-Length (важно для туннеля)
        if tr.file_id:
            fp = await _tg_file_path(track_id, tr.file_id)
            if fp:
                base = f"https://api.telegram.org/file/bot{settings.BOT_TOKEN}/"
                from urllib.parse import urljoin
                url = urljoin(base, fp)
                return await relay_response(url, rng, settings.PROXY_URL or None)

        # 3) не скачан — превью/онлайн: стримим чужой CDN через relay
        #    (раньше файл буферизовался целиком — TTFB доходил до 14 с)
        online = await _resolve_online_audio(track_id, tr)
        if online:
            return await relay_response(online, rng, settings.PROXY_URL or None)

        raise HTTPException(404, "трек недоступен онлайн — нажми «Скачать»")


@router.get("/diag/sources")
async def diag_sources(q: str = "баста сансара", user: CurrentUser = Depends(tg_user)):
    """Проверка источников с IP продакшна: поиск → ссылка → реальные байты."""
    import httpx
    from services.providers import hitmoz as hmz
    from services.providers import hotplayer as hpl
    from services.providers import zaycev as zyc

    out: dict = {}
    for name, prov in (("zaycev", zyc), ("hitmoz", hmz), ("hotplayer", hpl)):
        t0 = _time.time()
        try:
            cands = await prov.search(q, limit=3)
            row: dict = {"search_s": round(_time.time() - t0, 2), "found": len(cands),
                         "first": (f"{cands[0].artist} — {cands[0].title}"
                                   if cands else None)}
            if cands:
                t1 = _time.time()
                url = await prov.download_url(cands[0])
                row["url_s"] = round(_time.time() - t1, 2)
                if url:
                    try:
                        async with httpx.AsyncClient(timeout=25,
                                                     follow_redirects=True) as c:
                            r = await c.get(url, headers={
                                "Range": "bytes=0-65535",
                                "User-Agent": "Mozilla/5.0"})
                        row["mp3"] = {"status": r.status_code, "bytes": len(r.content),
                                      "type": r.headers.get("content-type")}
                    except Exception as e:
                        row["mp3"] = f"{type(e).__name__}: {str(e)[:60]}"
                else:
                    row["mp3"] = "no url"
            out[name] = row
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:80]}",
                         "search_s": round(_time.time() - t0, 2)}

    for name, url, params in (
            ("deezer", "https://api.deezer.com/search", {"q": q, "limit": 1}),
            ("itunes", "https://itunes.apple.com/search",
             {"term": q, "media": "music", "entity": "song", "limit": 1})):
        t0 = _time.time()
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(url, params=params)
            body = r.json()
            has = bool((body.get("data") or body.get("results") or [{}])[0])
            out[name] = {"status": r.status_code, "has_match": has,
                         "s": round(_time.time() - t0, 2)}
        except Exception as e:
            out[name] = {"error": type(e).__name__,
                         "s": round(_time.time() - t0, 2)}

    # карта доступности сайтов-аналогов с IP продакшна (параллельно)
    from urllib.parse import quote as _q

    async def _probe(name: str, url: str):
        t0 = _time.time()
        try:
            async with httpx.AsyncClient(
                    timeout=8, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                           "AppleWebKit/537.36 Chrome/126.0"}) as c:
                r = await c.get(url)
            text = r.text or ""
            return name, {"status": r.status_code, "len": len(text),
                          "s": round(_time.time() - t0, 2)}
        except Exception as e:
            return name, {"error": type(e).__name__,
                          "s": round(_time.time() - t0, 2)}

    sites = {
        "mp3party": "https://mp3party.net/search?q=" + _q(q),
        "get-tune": "https://get-tune.cc/search/?q=" + _q(q),
        "sefon": "https://sefon.pro/search/?q=" + _q(q),
        "z1": "https://z1.fm/search/" + _q(q),
        "musify": "https://musify.club/search?q=" + _q(q),
        "muzofond": "https://muzofond.fm/search/" + _q(q),
        "hotplayer": "https://hotplayer.ru/?s=" + _q(q),
        "mp3xa": "https://mp3xa.me/search?q=" + _q(q),
    }
    out["sites"] = dict(await asyncio.gather(
        *[_probe(n, u) for n, u in sites.items()]))
    return out


async def relay_response(url: str, rng: str | None, proxy: str | None):
    """Стримим чужой статичный файл, ДО старта тела зная его длину:
    pinggy (и любой туннель) плохо живёт с chunked-ответами без Content-Length."""
    import httpx
    cli = httpx.AsyncClient(timeout=httpx.Timeout(None, read=None, write=30, connect=30),
                            proxy=proxy)
    up = await cli.send(cli.build_request("GET", url, headers={"Range": rng} if rng else {}),
                        stream=True)
    if up.status_code >= 400:
        await up.aclose()
        await cli.aclose()
        raise HTTPException(404, "источник аудио недоступен")
    out = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=600"}
    if up.headers.get("content-length"):
        out["Content-Length"] = up.headers["content-length"]
    if up.headers.get("content-range"):
        out["Content-Range"] = up.headers["content-range"]
    ct = up.headers.get("content-type") or "audio/mpeg"

    async def gen():
        try:
            async for chunk in up.aiter_bytes(96 * 1024):
                yield chunk
        finally:
            await up.aclose()
            await cli.aclose()

    return StreamingResponse(gen(), status_code=up.status_code, media_type=ct, headers=out)


def _file_slice_gen(path, start: int, end: int):
    """Синхронный слайсер файла — starlette крутит его в threadpool."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(96 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
