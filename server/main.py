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


@app.get("/api/growth")
async def growth(key: str = ""):
    """Метрики роста: счётчики рекламных меток + новые юзеры.
    Защита секретом GROWTH_KEY (не для публичного доступа)."""
    from core.config import settings
    if key != settings.GROWTH_KEY:
        raise HTTPException(403, "нет доступа")
    from sqlalchemy import select, func
    from core.models import User, WaveKV
    from bot import runtime
    from datetime import timedelta
    from core.models import utcnow
    async with runtime.get_session() as s:
        rows = (await s.execute(select(WaveKV).where(
            WaveKV.key.like("start:%") | (WaveKV.key == "landing_visits")))).scalars().all()
        labels = {r.key: (r.value or {}).get("n", 0) for r in rows}
        total = (await s.execute(select(func.count(User.id)))).scalar()
        day = (await s.execute(select(func.count(User.id)).where(
            User.created_at >= utcnow() - timedelta(hours=24)))).scalar()
        week = (await s.execute(select(func.count(User.id)).where(
            User.created_at >= utcnow() - timedelta(days=7)))).scalar()
    return {"labels": labels, "users_total": int(total),
            "users_24h": int(day), "users_7d": int(week)}

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
