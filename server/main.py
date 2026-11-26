"""FastAPI приложение: REST API + статика Mini App."""
import asyncio
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.db import init_db
from music.api import router as music_api_router
from music.config import music_settings
from music.db import init_music_db
from server.routes import tracks as tracks_route
from server.routes import playlists as playlists_route
from server.routes import auth as auth_route
from server.routes import podcasts as podcasts_route
from server.routes import audiobooks as audiobooks_route

# INFO наших сервисов (провайдеры, релей, онлайн-звук) — в stderr под uvicorn
logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s",
                    stream=sys.stderr)

app = FastAPI(title="TGMusicBot API", docs_url="/api/docs", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# Статика мини-аппа: index.html — never-cache (иначе в Telegram-вебвью застревает
# старый app.js/index.html и после каждого обновления видна «та же ошибка»),
# а хешированные ассеты /v2/assets/* — кеш на год: имена содержат хеш содержимого,
# поэтому повторное открытие мини-аппа не перекачивает ~800КБ через медленный канал.
@app.middleware("http")
async def cache_static(request: Request, call_next):
    response = await call_next(request)
    p = request.url.path
    if p in ("/", "/v2", "/v2/", "/v2/index.html"):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    elif p.startswith("/v2/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response

@app.on_event("startup")
async def startup():
    await init_db()
    try:
        await init_music_db()
    except Exception:  # noqa: BLE001
        logging.getLogger("server").exception("music: таблицы не созданы")
    # биллинг: сид промокодов (sever1908 и др.)
    try:
        from services.billing import seed_promos
        async with runtime.get_session() as s:
            await seed_promos(s)
    except Exception:  # noqa: BLE001
        logging.getLogger("server").exception("billing: сид промокодов")
    # Релиз 1 (распределённые роли): при MEDIA_ROLE=app таблица очереди
    # media_download_jobs создаётся автоматически (create_all выше), а
    # локальный воркер Render работает как последний резерв — берёт джобы,
    # которые ждут дольше MEDIA_LOCAL_FALLBACK_SEC (основные воркеры
    # Botkeep/serv00 полнят очередь чаще и успевают первыми).
    if music_settings.media_role == "app":
        from music import mediajobs
        mediajobs.start_local_reserve()
    elif music_settings.media_role == "worker":
        # Встроенный poller для роли worker не допускаем: на хосте отдельно
        # работает run_worker.py и только он определяет реальные capabilities.
        pass
    elif music_settings.media_role == "local":
        # /download-bulk пишет media_download_jobs и должен иметь executor;
        # одиночные загрузки по-прежнему идут своим лёгким локальным путём.
        from music import mediajobs
        mediajobs.start_local_worker()
    # RSS baseline вместо SSH (на free-плане Render SSH закрыт платформой):
    # раз в 5 минут одна строка в логи — снимается render_logs.py по маркеру
    # server.mem. На Windows/локале /proc нет — цикл молчит.
    asyncio.create_task(_mem_log_loop())


def _read_rss_kb() -> int:
    """Текущий RSS процесса в КБ (Linux-контейнер), 0 если недоступно."""
    try:
        with open("/proc/self/status", encoding="ascii", errors="ignore") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0


async def _mem_log_loop():
    log = logging.getLogger("server.mem")
    while True:
        try:
            kb = _read_rss_kb()
            if kb:
                log.info("RSS %d MB", kb // 1024)
        except Exception:  # noqa: BLE001 — диагностика не должна падать
            pass
        await asyncio.sleep(300)

@app.on_event("shutdown")
async def shutdown():
    if music_settings.media_role in ("app", "local"):
        from music import mediajobs
        await mediajobs.stop_local_worker()

app.include_router(tracks_route.router)
app.include_router(music_api_router)
app.include_router(playlists_route.router)
app.include_router(playlists_route.likes_router)
app.include_router(playlists_route.artists_router)
app.include_router(auth_route.router)
app.include_router(podcasts_route.router)
app.include_router(audiobooks_route.router)
from server.routes import playlist_import as import_route  # noqa: E402
app.include_router(import_route.router)
from server.routes import wave as wave_route  # noqa: E402
app.include_router(wave_route.router)
from server.routes import billing as billing_route  # noqa: E402
app.include_router(billing_route.router)
from server.routes import ympx as ympx_route  # noqa: E402
app.include_router(ympx_route.router)

@app.get("/api/ping")
async def ping():
    from core.config import settings
    return {"ok": True, "build": settings.APP_BUILD}


_open_state: dict[int, float] = {}   # uid -> timestamp последнего авторизованного запроса


@app.middleware("http")
async def _count_app_opens(request, call_next):
    """Аналитика запусков мини-аппа: авторизованный запрос после паузы ≥30 мин
    = новый «открытый сеанс». Счётчики — wave_kv (opens:<uid>): total + дни.
    Пишем только на переходе «новый сеанс» — максимум пара записей на визит."""
    init = request.headers.get("x-init-data", "")
    uid = None
    if init and "user=" in init and "hash=" in init:
        try:
            import json as _json
            import urllib.parse as _up
            segs = dict(kv.split("=", 1) for kv in init.split("&") if "=" in kv)
            u = _json.loads(_up.unquote(segs.get("user", "")))
            uid = int(u.get("id", 0)) or None
        except Exception:
            uid = None
    if uid and request.method in ("GET", "POST"):
        import time as _time
        now = _time.time()
        last = _open_state.get(uid, 0.0)
        _open_state[uid] = now
        if now - last > 1800.0:
            try:
                from sqlalchemy import select as _sel
                from core.models import WaveKV as _WK
                from core.models import utcnow as _utcnow
                async with runtime.get_session() as s:
                    row = (await s.execute(_sel(_WK).where(_WK.key == f"opens:{uid}"))).scalar_one_or_none()
                    today = _utcnow().date().isoformat()
                    if row is None:
                        s.add(_WK(key=f"opens:{uid}",
                                  value={"days": {today: 1}, "total": 1, "last": today}))
                    else:
                        v = dict(row.value or {})
                        days = dict(v.get("days") or {})
                        days[today] = int(days.get(today, 0)) + 1
                        if len(days) > 30:
                            for k2 in sorted(days)[:-30]:
                                days.pop(k2, None)
                        v.update(days=days, total=int(v.get("total", 0)) + 1, last=today)
                        row.value = v
                    await s.commit()
            except Exception:
                pass
    return await call_next(request)


@app.get("/api/growth")
async def growth(key: str = ""):
    """Метрики роста: счётчики рекламных меток + новые юзеры.
    Защита секретом GROWTH_KEY (не для публичного доступа)."""
    from core.config import settings
    if key != settings.GROWTH_KEY:
        raise HTTPException(403, "нет доступа")
    from sqlalchemy import select, func
    from core.models import User, WaveKV, Track, PlayEvent
    from bot import runtime
    from datetime import timedelta
    from core.models import utcnow
    async with runtime.get_session() as s:
        rows = (await s.execute(select(WaveKV).where(
            WaveKV.key.like("start:%") | (WaveKV.key == "landing_visits")))).scalars().all()
        labels = {r.key: (r.value or {}).get("n", 0) for r in rows}
        total = (await s.execute(select(func.count(User.id)))).scalar()
        # реальные пользователи бота — id < 1e12 (гости мини-аппа ≥ 1e12)
        botq = select(func.count(User.id)).where(User.id < 1e12)
        guestq = select(func.count(User.id)).where(User.id >= 1e12)
        day_b = (await s.execute(botq.where(User.created_at >= utcnow() - timedelta(hours=24)))).scalar()
        day_g = (await s.execute(guestq.where(User.created_at >= utcnow() - timedelta(hours=24)))).scalar()
        week_b = (await s.execute(botq.where(User.created_at >= utcnow() - timedelta(days=7)))).scalar()
        tot_b = (await s.execute(botq)).scalar()
        tot_g = (await s.execute(guestq)).scalar()
        # нагрузка: джобы загрузок и треки за окно
        from core.models import DownloadJob
        async def _cnt(q):
            return int((await s.execute(q)).scalar() or 0)
        jd24 = await _cnt(select(func.count(DownloadJob.id)).where(
            DownloadJob.status == "done", DownloadJob.created_at >= utcnow() - timedelta(hours=24)))
        jf24 = await _cnt(select(func.count(DownloadJob.id)).where(
            DownloadJob.status == "failed", DownloadJob.created_at >= utcnow() - timedelta(hours=24)))
        jd7 = await _cnt(select(func.count(DownloadJob.id)).where(
            DownloadJob.status == "done", DownloadJob.created_at >= utcnow() - timedelta(days=7)))
        jf7 = await _cnt(select(func.count(DownloadJob.id)).where(
            DownloadJob.status == "failed", DownloadJob.created_at >= utcnow() - timedelta(days=7)))
        tr24 = await _cnt(select(func.count(Track.id)).where(
            Track.created_at >= utcnow() - timedelta(hours=24)))
        tr7 = await _cnt(select(func.count(Track.id)).where(
            Track.created_at >= utcnow() - timedelta(days=7)))
        errs = (await s.execute(select(DownloadJob.error, func.count(DownloadJob.id))
            .where(DownloadJob.status == "failed", DownloadJob.error.is_not(None),
                   DownloadJob.created_at >= utcnow() - timedelta(days=7))
            .group_by(DownloadJob.error).order_by(func.count(DownloadJob.id).desc())
            .limit(5))).all()
        srcs = (await s.execute(select(Track.source, func.count(Track.id))
            .where(Track.created_at >= utcnow() - timedelta(days=7))
            .group_by(Track.source).order_by(func.count(Track.id).desc())
            .limit(7))).all()
        # баланс звёзд: сумма чистых зачислений через Bot API getStarTransactions
        stars = None
        tok = settings.BOT_TOKEN
        if tok:
            try:
                import httpx
                total, n = 0, 0
                offset = 0
                async with httpx.AsyncClient(timeout=30) as hc:
                    while True:
                        r = await hc.get(f"https://api.telegram.org/bot{tok}/getStarTransactions",
                                         params={"offset": offset, "limit": 100})
                        res = r.json().get("result", {})
                        txs = res.get("transactions", [])
                        if not txs:
                            break
                        for tx in txs:
                            # amount — чистое зачисление боту (после комиссий)
                            if tx.get("source", {}).get("type") in ("invoice", None):
                                total += int(tx.get("amount", 0))
                            n += 1
                        offset = res.get("offset") or 0
                        if not offset or len(txs) < 100:
                            break
                stars = {"received_total": total, "transactions": n}
            except Exception:
                stars = None
        # «постоянные» юзеры: ≥2 входов в приложение И активность в боте
        from core.models import AuthCode
        logins = (await s.execute(select(AuthCode.user_id,
            func.count(AuthCode.id), func.count(func.distinct(func.date(AuthCode.created_at))))
            .group_by(AuthCode.user_id))).all()
        plays = (await s.execute(select(PlayEvent.user_id,
            func.count(PlayEvent.id), func.count(func.distinct(func.date(PlayEvent.started_at))),
            func.max(PlayEvent.started_at))
            .group_by(PlayEvent.user_id))).all()
        lp = {r[0]: (int(r[1]), int(r[2]), r[3]) for r in plays}
        engaged = []
        opens_rows = (await s.execute(select(WaveKV).where(
            WaveKV.key.like("opens:%")))).scalars().all()
        opens_map = {}
        for r in opens_rows:
            uid = int(r.key.split(":", 1)[1])
            v = r.value or {}
            opens_map[uid] = (int(v.get("total", 0)), int(v.get("last", "") == str(_utcnow().date())))
        for uid, lc, ld in logins:
            pc, pd, last_play = lp.get(uid, (0, 0, None))
            if lc >= 2 and (pc >= 3 or pd >= 2):
                engaged.append({"uid": int(uid), "logins": lc, "login_days": int(ld),
                                "plays": pc, "play_days": pd,
                                "last": str(last_play)[:16] if last_play else None})
        # «постоянные» = ≥2 открытия приложения (любой возврат — уже постоянство)
        returning = sorted(((uid, tot) for uid, (tot, _t) in opens_map.items() if tot >= 2),
                           key=lambda x: -x[1])
        returning_active = [r for r in returning
                            if lp.get(r[0], (0, 0, None))[0] >= 2]
        engaged.sort(key=lambda x: -(x["logins"] + x["plays"]))
        # диагностические распределения: кто сколько раз входил/слушал
        logins_top = sorted(logins, key=lambda r: -int(r[1]))[:5]
        plays_top = sorted(plays, key=lambda r: -int(r[1]))[:5]
        repeat_logins = sum(1 for _, lc, _ld in logins if lc >= 2)
        repeat_plays = sum(1 for u, _row in lp.items() if lp[u][0] >= 2)
        # промокоды: сколько активаций у каждого + последние
        from core.models import PromoCode, PromoRedemption
        promos = (await s.execute(select(PromoCode))).scalars().all()
        redemps = (await s.execute(select(PromoRedemption).order_by(PromoRedemption.at.desc()).limit(15))).scalars().all()
        last = (await s.execute(select(User.id, User.first_name, User.created_at)
            .order_by(User.created_at.desc()).limit(8))).all()
    return {"labels": labels,
            "users_bot_total": int(tot_b), "users_bot_24h": int(day_b),
            "users_bot_7d": int(week_b),
            "guests_total": int(tot_g), "guests_24h": int(day_g),
            "load": {"jobs_done_24h": jd24, "jobs_failed_24h": jf24,
                     "jobs_done_7d": jd7, "jobs_failed_7d": jf7,
                     "tracks_24h": tr24, "tracks_7d": tr7,
                     "top_errors": [{"err": (e or "")[:110], "n": int(c)} for e, c in errs],
                     "sources_7d": [{"src": s2, "n": int(c)} for s2, c in srcs]},
            "promos": [{"code": c.code, "used": int(c.used or 0), "max_uses": c.max_uses,
                        "tier": c.tier, "days": c.days, "active": bool(c.active)} for c in promos],
            "promo_redemptions": [{"code": r.code, "uid": int(r.user_id),
                                   "at": r.at.isoformat()[:16] if r.at else None} for r in redemps],
            "engaged": {"count": len(engaged),
                        "criteria": "≥2 входов в приложение + ≥3 прослушиваний или ≥2 дней",
                        "top": engaged[:10],
                        "repeat_logins_users": int(repeat_logins),
                        "repeat_plays_users": int(repeat_plays),
                        "logins_top": [{"uid": int(r[0]), "n": int(r[1])} for r in logins_top],
                        "plays_top": [{"uid": int(r[0]), "n": int(r[1]), "days": int(r[2])} for r in plays_top],
                        "app_opens_users": len(opens_map),
                        "returning_app_users": len(returning),
                        "returning_app_active": len(returning_active),
                        "returning_top": [{"uid": int(u), "opens": int(n)} for u, n in returning[:10]]},
            "stars": stars,
            "last_users": [{"id": r[0], "name": r[1], "at": str(r[2])} for r in last]}

@app.get("/health")
async def health():
    return {"ok": True}


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_not_found(rest: str):
    """Любой неизвестный /api/* отдаёт JSON 404 (а не HTML-страницу статики)."""
    raise HTTPException(404, f"нет такого API-эндпоинта: /api/{rest}")

# React-версия мини-аппа (v2), сборка Vite.
V2_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if V2_DIR.exists():
    app.mount("/v2", StaticFiles(directory=str(V2_DIR), html=True), name="v2")

@app.get("/")
async def root():
    """Корень ведёт на актуальный мини-апп (старый vanilla webapp удалён)."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/v2/")
