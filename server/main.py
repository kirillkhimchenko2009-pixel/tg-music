"""FastAPI приложение: REST API + статика Mini App."""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.db import init_db
from server.routes import tracks as tracks_route
from server.routes import playlists as playlists_route
from server.routes import auth as auth_route

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"

app = FastAPI(title="TGMusicBot API", docs_url="/api/docs", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# Статика мини-аппа не должна кэшироваться: иначе в Telegram-вебвью застревает
# старый app.js/index.html и после каждого обновления видна «та же ошибка».
@app.middleware("http")
async def nocache_static(request: Request, call_next):
    response = await call_next(request)
    p = request.url.path
    if p in ("/", "/index.html", "/app.js", "/styles.css",
             "/v2", "/v2/", "/v2/index.html"):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response

@app.on_event("startup")
async def startup():
    await init_db()

@app.on_event("shutdown")
async def shutdown():
    pass

app.include_router(tracks_route.router)
app.include_router(playlists_route.router)
app.include_router(auth_route.router)
from server.routes import playlist_import as import_route  # noqa: E402
app.include_router(import_route.router)

@app.get("/api/ping")
async def ping():
    return {"ok": True}

@app.get("/health")
async def health():
    return {"ok": True}


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_not_found(rest: str):
    """Любой неизвестный /api/* отдаёт JSON 404 (а не HTML-страницу статики)."""
    raise HTTPException(404, f"нет такого API-эндпоинта: /api/{rest}")

# React-версия мини-аппа (v2), сборка Vite — монтируется ДО vanilla-статики,
# иначе StaticFiles("/") перехватит и /v2/* тоже
V2_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if V2_DIR.exists():
    app.mount("/v2", StaticFiles(directory=str(V2_DIR), html=True), name="v2")

if WEBAPP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")
