"""Проверка lk.<uuid> ссылки «Мне нравится» анонимным api-фетчем."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

uuid = "d8758f78-0960-42dd-a4ac-92fb2c81afd0"
for path in (f"/playlist/lk.{uuid}", f"/playlists/lk.{uuid}",
             f"/playlist/{uuid}", f"/users/{uuid}/playlists/3"):
    url = f"https://api.music.yandex.net{path}"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        head = (r.text or "")[:220].replace("\n", " ")
        print(f"{path:<44} -> {r.status_code} | {head!r}")
        if r.status_code == 200:
            res = r.json().get("result") or {}
            data = res[0] if isinstance(res, list) else res
            tracks = data.get("tracks") or []
            print("    title:", data.get("title"), "| tracks:", len(tracks))
            if tracks:
                t0 = (tracks[0].get("track") or tracks[0])
                print("    t0:", t0.get("title"), "|",
                      [a.get("name") for a in (t0.get("artists") or [])])
    except Exception as e:  # noqa: BLE001
        print(f"{path:<44} -> ERR {str(e)[:110]}")
