"""Прогон запросов мини-аппа через публичный туннель (как из приложения)."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

BASE = (Path(__file__).parents[1] / "data" / "tunnel_url.txt").read_text().strip()
PROXY = "http://127.0.0.1:10809"

paths = [
    ("GET", "/api/library?limit=30"),
    ("GET", "/api/playlists"),
    ("GET", "/api/search?q=Медведь"),
]
with httpx.Client(proxy=PROXY, timeout=25) as c:
    for method, path in paths:
        try:
            r = c.request(method, BASE + path)
            body = (r.text or "")[:140].replace("\n", " ")
            print(f"{method} {path:<28} -> {r.status_code} | {body!r}")
        except Exception as e:  # noqa: BLE001
            print(f"{method} {path:<28} -> ERR {str(e)[:120]}")
    # стрим первого трека из библиотеки
    try:
        lib = c.get(BASE + "/api/library?limit=1").json()
        tracks = lib.get("tracks") or []
        if tracks:
            tid = tracks[0]["id"]
            r2 = c.get(f"{BASE}/api/stream/{tid}")
            print(f"GET /api/stream/{tid:<15} -> {r2.status_code} | "
                  f"ctype={r2.headers.get('content-type')} | len={r2.headers.get('content-length')}")
        else:
            print("библиотека пуста — стрим не проверить")
    except Exception as e:  # noqa: BLE001
        print("stream ERR", str(e)[:120])
