"""Точка входа воркера скачиваний (Botkeep / serv00) — Релиз 2/3 плана
«Vibe на трёх бесплатных серверах».

Только тянущая очередь из Neon: poll → claim → скачать (общий пайплайн
ingest.resolve) → отправить в чат напрямую (api.telegram.org) →
file_id в Neon → удалить файл. Без uvicorn и БЕЗ поллинга Telegram:
токен поллит только APP-процесс на Render.

Запуск:
    MEDIA_ROLE=worker MEDIA_WORKER_NAME=botkeep BOT_TOKEN=… \
    DATABASE_URL=… python run_worker.py

Опрос по умолчанию 3 с (основной воркер). Для serv00 — 25 с
(MEDIA_POLL_SEC=25), чтобы он подбирал джобы только при загруженности
основного воркера. Пустая очередь → цикл спит интервал, compute-часы
Neon расходуются экономно.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys

# Маркер сборки: печатается при старте (build=...). Если в консоли его нет —
# панель запустила старый код, загрузка архива не дошла до файлов.
WORKER_BUILD = "r31-20261124-saavn"


def _ensure_deps() -> None:
    """Pterodactyl пересоздаёт контейнер при каждом «Pulling Docker image»:
    ранее установленные pip-пакеты исчезают, и воркер падал с
    ModuleNotFoundError (dotenv и т.д.). Ставим requirements-worker.txt
    сами, перед импортами; на Debian-образах с PEP 668 добавляем
    --break-system-packages. Пац-эдж: даже если pip не сработает — падать
    будем дальше с честной ошибкой, а не молча."""
    import importlib.util
    import subprocess

    probe = ("dotenv", "httpx", "sqlalchemy", "aiogram")
    missing = [m for m in probe if importlib.util.find_spec(m) is None]
    if not missing:
        return
    req = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "requirements-worker.txt")
    print(f"[deps] нет пакетов: {', '.join(missing)} — ставлю {req}…", flush=True)
    for extra in ([], ["--break-system-packages"]):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check", *extra, "-r", req],
                           check=True)
            print("[deps] зависимости установлены", flush=True)
            return
        except Exception as e:
            print(f"[deps] pip install ({extra or 'обычный'}): "
                  f"{type(e).__name__}: {e}", flush=True)
    print("[deps] НЕ СМОГ установить зависимости — панель: Startup Command "
          "должен ставить их сам (pip install -r requirements-worker.txt)",
          flush=True)


_ensure_deps()


def _health_url(webapp_url: str) -> str:
    """Keepalive targets the service root even if WEBAPP_URL includes /v2/."""
    from urllib.parse import urlsplit

    parsed = urlsplit(webapp_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("WEBAPP_URL must be an absolute HTTP(S) URL")
    return f"{parsed.scheme}://{parsed.netloc}/health"


async def _keepalive() -> None:
    """Free-инстанс Render засыпает после ~15 мин без входящих HTTP:
    холодный старт ~1 мин, бот молчит, мини-апп «не открывается».
    Входящий пинг /health каждые 12 мин держит инстанс тёплым
    (~744 инстанс-часа/мес — внутри 750 бесплатных). Исходящий поллинг
    Telegram сон НЕ предотвращает — поэтому именно HTTP-пинг.
    Ошибки глушим: это страховка, а не критичный путь."""
    import httpx

    base = (os.getenv("WEBAPP_URL")
            or "https://tg-music-0en5.onrender.com/v2/").rstrip("/")
    health_url = _health_url(base)
    log = logging.getLogger("keepalive")
    logged = False
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                response = await client.get(health_url)
                response.raise_for_status()
                if not logged:
                    log.info("keepalive: %s %s", response.status_code, health_url)
                    logged = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if logged:
                    log.warning("keepalive failed: %s", exc)
                logged = False              # восстановление залогируем снова
            await asyncio.sleep(720)


async def main() -> None:
    # Воркер часто запускают руками/кроном без MEDIA_ROLE — семантика всё
    # равно воркерская, ставим дефолт (env в панелях главнее setdefault).
    os.environ.setdefault("MEDIA_ROLE", "worker")

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    log = logging.getLogger("run_worker")

    from core.config import settings
    if not settings.BOT_TOKEN:
        log.error("BOT_TOKEN не задан — воркер не сможет отправить файл в чат")
        raise SystemExit(1)
    if not os.getenv("DATABASE_URL"):
        log.error("DATABASE_URL не задан — очередь в Neon недоступна "
                  "(локальный SQLite не годится для распределённой очереди)")
        raise SystemExit(1)

    from music.db import init_music_db
    await init_music_db(seed_sources=False)

    from music import mediajobs
    from music.config import music_settings

    # ffmpeg нужен только джобам needs_ffmpeg (ytmusic/soundcloud).
    # Нет ffmpeg — честно сужаем caps, чтобы не хватать джобы и не падать:
    # их возьмёт воркер с ffmpeg или локальный резерв Render.
    caps = music_settings.worker_caps_ffmpeg
    if caps and shutil.which(music_settings.ffmpeg_bin) is None:
        log.warning("ffmpeg не найден (%r) — джобы needs_ffmpeg (YouTube/"
                    "SoundCloud) этот воркер брать не будет",
                    music_settings.ffmpeg_bin)
        caps = False

    poller = mediajobs.MediaJobPoller(caps_ffmpeg=caps)
    log.info("воркер %s стартует: role=%s poll=%.0fs caps_ffmpeg=%s db=%s build=%s",
             poller.name, music_settings.media_role, poller.poll_sec,
             poller.caps_ffmpeg, "Neon" if os.getenv("DATABASE_URL") else "local",
             WORKER_BUILD)
    keepalive = asyncio.create_task(_keepalive())
    try:
        await poller.run_forever()
    finally:
        keepalive.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # крон serv00/Botkeep перезапустит процесс; джобы с lease
        # вернутся в очередь по TTL — ничего не теряется
        pass
