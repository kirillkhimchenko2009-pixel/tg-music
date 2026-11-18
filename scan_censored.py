# -*- coding: utf-8 -*-
"""Одноразовая чистка кеша от «замученных» заливок (запуск на Botkeep).

У явных (18+) треков с кешем file_id/локальным файлом проверяет фонограмму
детектором censor.check (муты/бипы). Если заливка цензурная — file_id и
local_path очищаются, ставится censored=1: следующее «скачать» пойдёт мимо
ЯМ (оригиналы zaycev/YouTube/прочие), а плеер 18+ стримит только оригиналы.

Запуск в консоли Botkeep:  python scan_censored.py
Секреты не печатает; файл по file_id скачивается напрямую из API Telegram.
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from sqlalchemy import select  # noqa: E402

from core.db import AsyncSessionLocal  # noqa: E402
from core.models import Track  # noqa: E402
from services import censor as censor_mod  # noqa: E402

import httpx  # noqa: E402

TOKEN = os.getenv("BOT_TOKEN", "")


async def fetch_tg_file(file_id: str, dest: str) -> bool:
    """Скачать файл из Telegram по file_id (без бота, чистый HTTP API)."""
    if not TOKEN:
        return False
    base = f"https://api.telegram.org/bot{TOKEN}"
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.get(f"{base}/getFile", params={"file_id": file_id})
        fp = ((r.json() or {}).get("result") or {}).get("file_path")
        if not fp:
            return False
        with open(dest, "wb") as f:
            async with c.stream("GET", f"{base}/file/{fp}") as resp:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
    return True


async def main() -> None:
    checked = cleared = 0
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Track).where(
            (Track.explicit == True) | (Track.censored == True)))) .scalars().all()  # noqa: E712
        suspects = [t for t in rows if (t.file_id or t.local_path)]
        print(f"подозреваемых (18+/censored с кешем): {len(suspects)}")
        for t in suspects:
            tmp = None
            try:
                if t.local_path and os.path.exists(t.local_path):
                    path = t.local_path
                else:
                    if not t.file_id:
                        continue
                    fd, tmp = tempfile.mkstemp(suffix=".mp3")
                    os.close(fd)
                    if not await fetch_tg_file(t.file_id, tmp):
                        print(f"  skip (file_id не скачался): id={t.id}")
                        continue
                    path = tmp
                chk = await censor_mod.check(path)
                checked += 1
                if chk.get("censored"):
                    t.file_id = ""
                    t.local_path = None
                    t.censored = True
                    await s.commit()
                    cleared += 1
                    print(f"  ЦЕНЗУРА (бипы={chk.get('beeps', 0)} "
                          f"муты={chk.get('mutes', 0)}): "
                          f"{t.artist} — {t.title}: кеш очищен")
            except Exception as e:  # noqa: BLE001
                print(f"  ошибка на id={t.id}: {type(e).__name__}: {e}")
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
    print(f"итог: проверено {checked}, очищено {cleared}")


asyncio.run(main())
