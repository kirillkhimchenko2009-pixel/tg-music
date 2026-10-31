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
from services.providers.base import TrackMeta
from services.providers.youtube import YouTubeProvider

router = APIRouter(prefix="/api", tags=["tracks"])
yt = YouTubeProvider()
JOB_STATUS: dict[str, dict] = {}
log = __import__("logging").getLogger("routes.tracks")


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
    t = None
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

            async def send_preview() -> bool:
                """В чат должен прийти хоть какой-то файл: откуда не досталось
                полное аудио — отправляем 30-сек фрагмент с пометкой."""
                from bot.runtime import send_preview_file
                ok = await send_preview_file(bot, user_id, t)
                if ok:
                    st.update(status="done", progress=100.0, telegram=True,
                              preview=True)
                return ok

            res = await ingest.resolve(s, meta_of(t), quality, user_id=user_id,
                                       on_progress=on_progress)
            if res.status == "not_found" or not (res.file_path or res.track):
                if await send_preview():
                    return
                st.update(status="error", error=res.error or "не удалось скачать")
                return
            target = res.track or (await catalog.get_track(s, track_id))
            if res.file_path and target and res.file_path.exists():
                from bot.runtime import send_audio_file, save_file_id_retry
                fid = await send_audio_file(bot, user_id, target, res.file_path)
                st.update(status="done", progress=100.0,
                          telegram=fid is not None)
                # кеш file_id обязательно пишем: иначе следующий раз качается заново
                if fid:
                    await save_file_id_retry(target.id, fid, res.quality,
                                             size=res.file_path.stat().st_size,
                                             local_path=res.file_path.as_posix())
            elif target and target.file_id:
                from bot.runtime import send_track_cached
                st.update(status="done", progress=100.0,
                          telegram=True if await send_track_cached(bot, user_id, target) else False)
            else:
                if await send_preview():
                    return
                st.update(status="error", error="файл не доступен")
    except Exception as e:  # noqa: BLE001
        # часто тут TelegramEntityTooLarge (>50 MB) — превью-файл крошечный,
        # его отправка всё равно реально сработает
        try:
            if t is not None:
                from bot.runtime import send_preview_file
                if await send_preview_file(bot, user_id, t):
                    st.update(status="done", progress=100.0, telegram=True,
                              preview=True)
                    return
        except Exception:
            pass
        st.update(status="error", error=str(e)[:200])

@router.get("/jobs/{job_id}")
async def job_status(job_id: str, user: CurrentUser = Depends(tg_user)):
    status = JOB_STATUS.get(job_id)
    if not status:
        raise HTTPException(404, "нет такой задачи")
    return status

# ---------------- потоковый плеер ----------------
_FILE_PATH_CACHE: dict[int, tuple[str, float]] = {}    # track_id -> (tg file_path, expire)
_AUDIO_URL_CACHE: dict[int, tuple[tuple[str, str, bool], float]] = {}  # id -> ((url, kind, 18+), expire)


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
    """Лёгкий JSON со ссылкой на поток (нативное воспроизведение, без blob).
    kind подсказывает UI, что играть: file (скачано/ТГ) | online (полный)
    | preview (30-сек превью — не нашлось полной версии)."""
    async with runtime.get_session() as s:
        t = await catalog.get_track(s, track_id)
        if not t:
            raise HTTPException(404, "нет такого трека")
        kind = "file" if (t.file_id or t.local_path) else "online"
        resolved = None
        if kind == "online":
            # резолвим ЗДЕСЬ (сессия нужна ym_source): прогрев кеша до первого
            # Range от <audio> + бейдж в UI
            resolved = await _resolve_online_audio(track_id, t, s, int(user.id))
            if resolved and resolved[1] == "preview":
                kind = "preview"
    return {"url": f"/api/stream/{track_id}?t={make_stream_token(int(user.id), track_id)}",
            "kind": kind}


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


async def _resolve_online_audio(track_id: int, t: Track,
                                session=None, user_id: int | None = None) -> tuple[str, str] | None:
    """Аудио для нескачанных треков. ПРИОРИТЕТ — ПОЛНЫЙ звук:
    Яндекс.Музыка (если юзер дал /token — весь каталог)
    → zaycev.net (mp3-320, Range) → hitmoz → hotplayer
    → YouTube (InnerTube → yt-dlp). Ничего не нашлось — быстрый фолбэк
    на 30-сек превью (Deezer → iTunes, ~1 с). Возвращает (url, kind)."""
    hit = _AUDIO_URL_CACHE.get(track_id)
    if hit and hit[1] > _time.time():
        return hit[0]

    async def ym_source(query: str) -> tuple[str, str, bool] | None:
        """Полный трек с Яндекс.Музыки. Третий элемент — флаг 18+ найденного
        кандидата: такие фонограммы у ЯМ бывают «чистыми» (слова замазаны)."""
        if session is None or user_id is None:
            return None
        try:
            from services.authstore import get_token
            tok = await get_token(session, int(user_id), "ym")
            if not tok:
                return None
            from services.matching import best_match
            from services.ingest import _clean_query
            from services.providers.yandex import YandexProvider
            meta = TrackMeta(source="yandex", external_id="", title=t.title or "",
                             artist=t.artist or "", duration=int(t.duration or 0))
            cands = await YandexProvider(tok).search(_clean_query(query), limit=8)
            m = best_match(meta, cands, min_score=0.5)
            if not m:
                return None
            expl18 = bool(getattr(m, "explicit", False))
            if expl18 and not bool(getattr(t, "explicit", False)):
                # старая строка БД без флага — пометим, чтобы и скачивание,
                # и последующие проигрыши получали приоритет оригиналов
                try:
                    t.explicit = True
                    await session.commit()
                except Exception:
                    pass
            url = await YandexProvider(tok).track_url(m, "")   # 320/aac — стримабельно
            if url:
                log.info("онлайн-звук «%s» → yandex: %s (18+: %s)",
                         query, m.display(), expl18)
                return url, "yandex", expl18
        except Exception as e:
            log.info("онлайн-звук yandex не удался: %s", type(e).__name__)
        return None

    async def direct_sources(query: str) -> tuple[str, str] | None:
        """Полные треки с прямых сайтов (mp3, Range-стрим): zaycev → hitmoz → hotplayer."""
        from services.matching import best_match
        from services.ingest import _clean_query
        from services.providers import hitmoz as hzm
        from services.providers import hotplayer as hpl
        from services.providers import zaycev as zay
        meta = TrackMeta(source="zaycev", external_id="", title=t.title or "",
                         artist=t.artist or "", duration=int(t.duration or 0))
        cquery = _clean_query(query)
        for prov, name in ((zay, "zaycev"), (hzm, "hitmoz"), (hpl, "hotplayer")):
            try:
                cands = await prov.search(cquery, limit=6)
                if not cands:
                    continue
                m = best_match(meta, cands, min_score=0.5)
                # guard по артисту обязателен — сайты отдают тёзок-однофамильцев;
                # если матч слабый, допускаем первого результата только с чистым артистом
                if m and not hzm.artist_guard(meta, m):
                    m = None
                if not m and hzm.artist_guard(meta, cands[0]):
                    m = cands[0]
                if not m:
                    continue
                url = await prov.download_url(m)
                if url:
                    log.info("онлайн-звук «%s» → %s: %s", cquery, name, m.display())
                    return url, name, False
            except Exception as e:
                log.info("онлайн-звук %s не удался: %s", name, type(e).__name__)
        return None

    q = f"{t.artist or ''} {t.title or ''}".strip()
    full = None
    explicit = bool(getattr(t, "explicit", False))
    if q:
        # параллельно ЯМ и прямые сайты; таймауты раздельны — зависший ЯМ больше
        # не выбрасывает готовые результаты zaycev (раньше умирал общий gather)
        t_ym = asyncio.create_task(ym_source(q))
        t_dir = asyncio.create_task(direct_sources(q))
        if explicit:
            # 18+: у ЯМ фонограммы бывают «чистые» (замазанные слова) —
            # у прямых сайтов обычно оригиналы, они первые; ЯМ-задача бежит
            # параллельно и её результат забирается как запасной
            try:
                full = await asyncio.wait_for(asyncio.shield(t_dir), timeout=32)
            except Exception:
                full = None
            if full:
                if not t_ym.done():
                    t_ym.cancel()
            else:
                try:
                    full = await asyncio.wait_for(asyncio.shield(t_ym), timeout=10)
                except Exception:
                    full = None
        else:
            try:
                full = await asyncio.wait_for(asyncio.shield(t_ym), timeout=13)
            except Exception:
                full = None
            if full and full[2]:
                # ЯМ подтвердил 18+ (флаг в БД был не проставлен) —
                # прямые сайты могут отдать оригинальную фонограмму
                log.info("онлайн-звук: ЯМ 18+ «%s» — пробую оригинал с прямых сайтов",
                         q[:46])
                try:
                    full2 = await asyncio.wait_for(asyncio.shield(t_dir), timeout=32)
                    if full2:
                        full = full2
                except Exception:
                    pass
            if full:
                if not t_dir.done():
                    t_dir.cancel()
            else:
                t_ym.cancel()
                log.info("онлайн-звук: ЯМ не дал результата (%s) — фолбэк на прямые сайты",
                         q[:46])
                try:
                    full = await asyncio.wait_for(t_dir, timeout=32)
                except Exception:
                    full = None
    if not full:
        # YouTube — InnerTube (быстро) → yt-dlp как запасной
        try:
            vid = t.external_id if (t.source or "") == "ytmusic" else ""
            url = await _yt_audio_url(vid=vid, query=q)
            if url:
                full = (url, "youtube", False)
        except Exception:
            full = None
    if not full:
        # превью-фолбэк: мгновенный старт, но 30 секунд
        dz, it = await asyncio.gather(_deezer_preview(t), _itunes_preview(t),
                                      return_exceptions=True)
        url = (dz if isinstance(dz, str) else None) or (it if isinstance(it, str) else None)
        if url:
            full = (url, "preview", False)
    if full:
        _AUDIO_URL_CACHE[track_id] = (full, _time.time() + 35 * 60)
    return full


async def _deezer_preview(t: Track) -> str | None:
    """Анонимное превью (30 сек) с Deezer — работает без токенов и без VPN."""
    from services.preview import deezer_preview
    return await deezer_preview(t.artist or "", t.title or "")


async def _itunes_preview(t: Track) -> str | None:
    """30-сек превью из iTunes Search API (CDN Apple, без токенов)."""
    from services.preview import itunes_preview
    return await itunes_preview(t.artist or "", t.title or "")


async def _yt_audio_url(vid: str, query: str = "") -> str | None:
    """Аудио-URL конкретного видео (или результат InnerTube-поиска):
    InnerTube (быстро, без yt-dlp) → yt-dlp (с прокси при необходимости)."""
    if vid:
        try:
            from services.providers import yt_stream
            got = await yt_stream.audio_url(vid)
            if got:
                return got["url"]
        except Exception:
            pass
    if not vid and query:
        # InnerTube-поиск → player: работает с датацентровых IP (Render),
        # yt-dlp там часто ловит бот-чек YouTube
        try:
            from services.providers import yt_stream as _yts
            found = await asyncio.wait_for(_yts.search_video_id(query), timeout=25)
            if found:
                got = await _yts.audio_url(found)
                if got:
                    log.info("онлайн-звук youtube «%s» (innertube, %s)",
                             query[:60], got.get("client"))
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

        # 3) не скачан — ПОЛНЫЙ звук онлайн (YM → zaycev → hitmoz → hotplayer → YT),
        #    иначе 30-сек превью; чужой CDN стримим через relay с Range-пробросом
        resolved = await _resolve_online_audio(track_id, tr, s, int(user.id))
        if resolved:
            return await relay_response(resolved[0], rng, settings.PROXY_URL or None)

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

    # YouTube: InnerTube play-проба известного видео + поиск по запросу
    t0 = _time.time()
    try:
        from services.providers import yt_stream
        got = await asyncio.wait_for(yt_stream.audio_url("jNQXAC9IVRw"), timeout=40)
        yrow: dict = {"s": round(_time.time() - t0, 2)}
        if got and got.get("url"):
            yrow["player"] = f"ok client={got.get('client')} itag={got.get('itag')}"
            try:
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.get(got["url"], headers={"Range": "bytes=0-65535"})
                yrow["bytes"] = {"status": r.status_code, "n": len(r.content),
                                 "type": r.headers.get("content-type")}
            except Exception as e:
                yrow["bytes"] = f"{type(e).__name__}: {str(e)[:60]}"
        else:
            yrow["player"] = "no_url"
        t1 = _time.time()
        srch = await asyncio.wait_for(yt_stream.search_video_id(q), timeout=30)
        yrow["search"] = {"found": bool(srch), "vid": srch or None,
                          "s": round(_time.time() - t1, 2)}
        if srch:
            g2 = await asyncio.wait_for(yt_stream.audio_url(srch), timeout=40)
            yrow["search"]["audio"] = "ok" if (g2 and g2.get("url")) else "no_url"
        out["youtube"] = yrow
    except Exception as e:
        out["youtube"] = {"error": type(e).__name__,
                          "s": round(_time.time() - t0, 2)}

    # Яндекс.Музыка: токен + живой пробинг поиска/ссылки/байтов
    try:
        from services.authstore import get_token
        from services.providers.yandex import YandexProvider as _Ymx
    except Exception as e:                                     # noqa: BLE001
        out["yandex"] = {"error": type(e).__name__}
    else:
        try:
            async with runtime.get_session() as s:
                ym_tok = await get_token(s, int(user.id), "ym")
            yrow: dict = {"token": bool(ym_tok)}
            if ym_tok:
                t0 = _time.time()
                res = await asyncio.wait_for(_Ymx(ym_tok).search(q[:80], limit=3),
                                             timeout=35)
                yrow["search"] = {
                    "s": round(_time.time() - t0, 2), "found": len(res),
                    "first": (f"{res[0].artist} — {res[0].title}" if res else None)}
                # RAW-пробинг того же запроса без библиотеки (aiohttp глотает тела)
                try:
                    async with httpx.AsyncClient(timeout=20) as c0:
                        r0 = await c0.get(
                            "https://api.music.yandex.ru/search",
                            params={"text": q[:64], "type": "track",
                                    "nocorrect": "false", "page": "0"},
                            headers={"Authorization": "OAuth " + ym_tok,
                                     "User-Agent": "Yandex-Music-API"})
                        body = r0.json()
                        res_j = body.get("result") or {}
                        tr_j = (res_j.get("tracks") or {}).get("results") or []
                        yrow["raw"] = {
                            "status": r0.status_code, "tracks": len(tr_j),
                            "err": res_j.get("error"),
                            "body_head": ("" if tr_j else r0.text[:170])}
                except Exception as e:          # noqa: BLE001
                    yrow["raw"] = f"{type(e).__name__}: {str(e)[:80]}"
                if res:
                    t1 = _time.time()
                    u = await asyncio.wait_for(_Ymx(ym_tok).track_url(res[0], ""),
                                               timeout=35)
                    yrow["url_s"] = round(_time.time() - t1, 2)
                    if u:
                        try:
                            async with httpx.AsyncClient(timeout=20) as c:
                                r = await c.get(u, headers={
                                    "Range": "bytes=0-65535",
                                    "User-Agent": "Yandex-Music-API",
                                    "Referer": "https://music.yandex.ru/"})
                            yrow["mp3"] = {"status": r.status_code,
                                           "bytes": len(r.content),
                                           "type": r.headers.get("content-type")}
                        except Exception as e:
                            yrow["mp3"] = f"{type(e).__name__}: {str(e)[:70]}"
                    else:
                        yrow["mp3"] = "no url"
            out["yandex"] = yrow
        except Exception as e:                                 # noqa: BLE001
            out["yandex"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}

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
    from urllib.parse import urlsplit
    host = urlsplit(url).netloc
    # браузерный вид обязателен: без него mp3-CDN отвечает 200 на весь файл
    # (Range игнорируется) — скраб будет качать файл целиком
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Referer": f"https://{host}/"}
    if rng:
        hdrs["Range"] = rng
    cli = httpx.AsyncClient(timeout=httpx.Timeout(None, read=None, write=30, connect=30),
                            proxy=proxy, follow_redirects=True)
    up = await cli.send(cli.build_request("GET", url, headers=hdrs), stream=True)
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
