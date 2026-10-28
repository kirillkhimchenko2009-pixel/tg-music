"""E2E мини-апп: POST /api/download + опрос /api/jobs/<id> (как в приложении)."""
import sys, json, time, hmac, hashlib, urllib.parse, asyncio
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from core.config import settings

UID = 5339474152   # твой telegram-аккаунт из логов бота

def build_init_data(uid: int) -> str:
    user = json.dumps({"id": uid, "first_name": "Тест", "username": "tester"},
                      separators=(",", ":"), ensure_ascii=False)
    pairs = {"auth_date": str(int(time.time())), "chat_type": "private",
             "chat_instance": "-1001", "user": user,
             "signature": "c2lnbmF0dXJl"}
    s = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs)
                  if k not in ("hash", "signature"))
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(),
                      hashlib.sha256).digest()
    hv = hmac.new(secret, s.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**pairs, "hash": hv})

async def main():
    h = {"X-Init-Data": build_init_data(UID)}
    async with httpx.AsyncClient(timeout=30) as c:
        lib = (await c.get("http://127.0.0.1:8000/api/library?limit=1",
                           headers=h)).json()
        tracks = lib.get("tracks") or []
        if not tracks:
            print("библиотека пуста")
            return
        tid = tracks[0]["id"]
        print("трек:", tracks[0]["title"], "id", tid,
              "| ready:", tracks[0]["ready"], "| q:", tracks[0].get("quality"))
        r = await c.post(f"http://127.0.0.1:8000/api/download?track_id={tid}",
                         headers=h)
        print("POST /download ->", r.status_code, r.text)
        if r.status_code != 200:
            return
        job = r.json()["job"]
        for i in range(40):
            await asyncio.sleep(2)
            j = (await c.get(f"http://127.0.0.1:8000/api/jobs/{job}", headers=h)).json()
            st = j.get("status")
            print(f"  job {i}: {st} {j.get('progress', 0):.0f}% {j.get('error') or ''}")
            if st in ("done", "error"):
                break

asyncio.run(main())
