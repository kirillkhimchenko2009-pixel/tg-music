# -*- coding: utf-8 -*-
"""Бэкап/восстановление SQLite-базы Vibe в приватный HF Dataset.

Диск бесплатного Space эфемерный: на рестарте контейнера файлы пропадают.
Поэтому: restore при старте, watch — выгрузка music.db каждые 15 минут
(и сразу при SIGTERM). Требуется секрет HF_TOKEN (write) в настройках Space.
"""
import asyncio
import os
import shutil
import signal
import sys
import time

DB = "/app/data/music.db"
DATA_DIR = "/app/data"
REPO = os.getenv("BACKUP_REPO", "")          # например: "username/vibe-backup"
TOKEN = os.getenv("HF_TOKEN", "")
INTERVAL = int(os.getenv("BACKUP_INTERVAL_MIN", "15")) * 60

log = lambda m: print(f"[backup] {m}", flush=True)


def _client():
    from huggingface_hub import HfApi
    return HfApi(token=TOKEN)


def restore() -> None:
    if not (REPO and TOKEN):
        log("BACKUP_REPO/HF_TOKEN не заданы — бэкап выключен")
        return
    if os.path.exists(DB):
        log(f"локальная база уже есть ({os.path.getsize(DB)} байт) — не перезаписываю")
        return
    try:
        from huggingface_hub import hf_hub_download
        os.makedirs(DATA_DIR, exist_ok=True)
        p = hf_hub_download(repo_id=REPO, filename="music.db", repo_type="dataset", token=TOKEN)
        shutil.copyfile(p, DB)
        log(f"восстановлено из {REPO}: {os.path.getsize(DB)} байт")
    except Exception as e:
        log(f"нет бэкапа (первый запуск?) — создаю пустую базу: {type(e).__name__}")


def save() -> None:
    if not (REPO and TOKEN and os.path.exists(DB)):
        return
    try:
        api = _client()
        api.upload_file(
            path_or_fileobj=DB, path_in_repo="music.db",
            repo_id=REPO, repo_type="dataset", token=TOKEN,
            commit_message=f"auto backup {time.strftime('%Y-%m-%d %H:%M')}")
        log(f"сохранено в {REPO} ({os.path.getsize(DB)} байт)")
    except Exception as e:
        log(f"ошибка сохранения: {type(e).__name__} {e}")


async def watch() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL)
        except asyncio.TimeoutError:
            save()
    save()          # финальное сохранение при остановке
    log("watcher остановлен")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "restore":
        restore()
    elif mode == "watch":
        asyncio.run(watch())
    else:
        print("usage: backup.py restore|watch")
