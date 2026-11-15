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
WORKER_BUILD = "r2fix1-20261115"


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
    await poller.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # крон serv00/Botkeep перезапустит процесс; джобы с lease
        # вернутся в очередь по TTL — ничего не теряется
        pass
