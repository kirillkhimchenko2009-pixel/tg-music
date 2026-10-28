"""E2E: поиск в мини-аппе + POST /download для НЕскачанного трека."""
import sys, json, time, hmac, hashlib, urllib.parse, asyncio
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from core.config import settings

UID = 5339474152

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
    base = "http://127.0.0.1:8000"
    async with httpx.AsyncClient(timeout=30) as c:
        s = (await c.get(f"{base}/api/search?q=Bardak Медведь", headers=h)).json()
        print("local:", [(t["title"][:30], t["ready"]) for t in s.get("local") or []][:3])
        web = s.get("web") or []
        print("web:", [(t["title"][:30], t["source"] if "source" in t else "?",
                        t["ready"]) for t in web][:5])
        if not web:
            print("нет веб-результатов")
            return
        cand = next((t for t in web if not t["ready"]), None) or web[0]
        tid = cand["id"]
        print("качаем:", cand["title"], "id", tid)
        r = await c.post(f"{base}/api/download?track_id={tid}", headers=h)
        print("POST ->", r.status_code, r.text)
        job = r.json()["job"]
        for i in range(60):
            await asyncio.sleep(2)
            j = (await c.get(f"{base}/api/jobs/{job}", headers=h)).json()
            st = j.get("status")
            print(f"  job {i}: {st} {j.get('progress', 0):.0f}% {j.get('error') or ''}"
                  f" tg={j.get('telegram')}")
            if st in ("done", "error"):
                break

asyncio.run(main())
