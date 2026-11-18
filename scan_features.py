# -*- coding: utf-8 -*-
"""Бэкфилл аудио-анализа каталога (запуск на Botkeep).

Волна умеет подстраиваться под настроения («бодрое / энергичное / грустное»)
по реальному звуку (BPM, энергия, валентность, танцевальность), но новые
признаки считаются только для свежих скачиваний. Этот скрипт проходит по уже
закешированным трекам (analyzed_at IS NULL) и досчитывает их.

Запуск в консоли Botkeep:  python scan_features.py
Повторный запуск безопасен: берутся только необработанные треки, поэтому
скрипт можно прерывать (Ctrl+C) и продолжать. Секреты не печатает; файл по
file_id скачивается напрямую из API Telegram и удаляется после анализа.
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from sqlalchemy import or_, select  # noqa: E402

from core.db import AsyncSessionLocal  # noqa: E402
from core.models import Track  # noqa: E402
from services import audio_features  # noqa: E402

import httpx  # noqa: E402

TOKEN = os.getenv("BOT_TOKEN", "")
LIMIT = int(os.getenv("FEATURES_SCAN_LIMIT", "300"))   # треков за прогон


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
    done = skipped = failed = 0
    async with AsyncSessionLocal() as s:
        q = (select(Track)
             .where(Track.analyzed_at.is_(None))
             .where(or_(Track.local_path.is_not(None), Track.file_id.is_not(None)))
             .order_by(Track.downloads.desc(), Track.id.desc())
             .limit(LIMIT))
        rows = (await s.execute(q)).scalars().all()
        print(f"к анализу: {len(rows)} (лимит {LIMIT}, повторный запуск продолжит)")
        for i, t in enumerate(rows, 1):
            tmp = None
            try:
                if t.local_path and os.path.exists(t.local_path):
                    path = t.local_path
                else:
                    if not t.file_id:
                        skipped += 1
                        continue
                    fd, tmp = tempfile.mkstemp(suffix=".mp3")
                    os.close(fd)
                    if not await fetch_tg_file(t.file_id, tmp):
                        print(f"  skip (file_id не скачался): id={t.id}")
                        skipped += 1
                        continue
                    path = tmp
                feats = await asyncio.to_thread(audio_features.analyze, str(path))
                if not feats:
                    print(f"  skip (не декодировался): id={t.id}")
                    skipped += 1
                    continue
                audio_features.apply_to_track(t, feats)
                await s.commit()
                done += 1
                if i % 25 == 0 or i == len(rows):
                    print(f"  {i}/{len(rows)}: bpm={feats['bpm']:.0f} "
                          f"energy={feats['energy']:.2f} valence={feats['valence']:.2f} "
                          f"dance={feats['danceability']:.2f} — {t.artist} — {t.title}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  ошибка на id={t.id}: {type(e).__name__}: {e}")
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
    print(f"итог: проанализировано {done}, пропущено {skipped}, ошибок {failed}")
    if done >= LIMIT:
        print("лимит прогона выбран — запустите ещё раз, чтобы продолжить")


asyncio.run(main())
