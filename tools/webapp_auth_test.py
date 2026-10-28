"""Имитация реального клиента мини-аппа: initData с signature-полем."""
import sys, json, time, hmac, hashlib, urllib.parse
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings
import httpx

def build_init_data(uid: int, with_signature: bool = True) -> str:
    user = json.dumps({"id": uid, "first_name": "Тест", "username": "tester"},
                      separators=(",", ":"), ensure_ascii=False)
    pairs = {"auth_date": str(int(time.time())), "chat_type": "private",
             "chat_instance": "-1001", "user": user}
    if with_signature:
        pairs["signature"] = "c2lnbmF0dXJl"     # произвольное значение
    # data-check-string: по правилам Telegram hash+signature в подписи не участвуют
    s = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs)
                  if k not in ("hash", "signature"))
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(),
                      hashlib.sha256).digest()
    hv = hmac.new(secret, s.encode(), hashlib.sha256).hexdigest()
    q = urllib.parse.urlencode(
        {**pairs, "hash": hv}, doseq=False, quote_via=urllib.parse.quote)
    return q

base = "http://127.0.0.1:8000"
h = {"Content-Type": "application/json"}
for name, uid, sig in (("с signature", 5339474152, True),
                       ("без signature", 5339474152, False),
                       ("битая подпись", 5339474152, None)):
    if sig is None:
        init = build_init_data(uid)[:40] + "&hash=deadbeef"
    else:
        init = build_init_data(uid, with_signature=sig)
    r = httpx.get(f"{base}/api/library?limit=3",
                  headers={**h, "X-Init-Data": init}, timeout=10)
    print(f"[{name}] library -> {r.status_code} | {r.text[:80]!r}")

r = httpx.get(f"{base}/api/playlists",
              headers={**h, "X-Init-Data": build_init_data(5339474152)}, timeout=10)
print("[с signature] playlists ->", r.status_code, "|", r.text[:100])
