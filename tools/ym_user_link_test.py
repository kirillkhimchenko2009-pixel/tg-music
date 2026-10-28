"""Сквозная проверка: parse + fetch по ссылке пользователя."""
import sys, asyncio
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.links import parse
from services.providers import yandex as ymx

URL = ("https://music.yandex.ru/playlists/lk.d8758f78-0960-42dd-a4ac-92fb2c81afd0"
       "?utm_source=desktop&utm_medium=copy_link")

link = parse(URL)
print("parsed:", link)

async def main():
    title, metas = await ymx.anon_playlist_by_uuid(link.id)
    print(f"title: {title} | tracks: {len(metas)}")
    for m in metas[:5]:
        print("   ", m.display())

asyncio.run(main())
