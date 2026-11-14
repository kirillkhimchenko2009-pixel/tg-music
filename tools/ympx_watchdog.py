# -*- coding: utf-8 -*-
"""Сторож ympx-туннеля: nurl всегда жив, адрес синхронизирован с продом.

Раз в 30 секунд:
1) нет процесса nurl.exe — перезапускаем отсоединённо (лог: data/nurl.log);
2) вытаскиваем публичный URL из лога; если он изменился — пишем
   data/ympx_base_url.txt и, когда найден ключ Render (env RENDER_KEY_FILE,
   иначе %TEMP%\\opencode\\render_key.txt), PATCH YMPX_BASE + триггер деплоя
   (Render API доступен только через локальный прокси 10809).

Так перезапуск туннеля или ребут машины не оставляют прод-импорт
Яндекс.Музыки на произвол судьбы: новый адрес сам попадает на прод.
Запуск: python tools/ympx_watchdog.py  (вечный, лучше Start-Process).
"""
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
NURL = Path(os.getenv("LOCALAPPDATA", "")) / "nurl" / "bin" / "nurl.exe"
URL_LOG = ROOT / "data" / "nurl.log"
STATE = ROOT / "data" / "ympx_base_url.txt"
PORT = "8811"
LOOP_SEC = 30

SRV = "srv-dbgsojl9el5s73dfdotg"
CLOUD = "https://vibe-music-dk6c.onrender.com"
PROXY = "http://127.0.0.1:10809"
def _key_path() -> Path:
    """env RENDER_KEY_FILE → E:\\Temp\\opencode → %LOCALAPPDATA%\\Temp."""
    cands = [Path(os.getenv("RENDER_KEY_FILE", "")),
             Path(r"E:\Temp\opencode\render_key.txt"),
             Path(os.getenv("LOCALAPPDATA", "")) / "Temp" / "opencode" / "render_key.txt"]
    for c in cands:
        if c and c.is_file():
            return c
    return cands[-1]


KEY_FILE = _key_path()

URL_RE = re.compile(r"https://[a-z0-9]+\.nurl\.pw")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(ROOT / "data" / "ympx_watchdog.log",
                                  encoding="utf-8")])
log = logging.getLogger("ympx-watchdog")


def start_nurl() -> None:
    """nurl отсоединённо: cmd-обёртка с редиректом в data/nurl.log."""
    log.info("перезапускаю nurl (порт %s)", PORT)
    cmd = f'cd /d "{ROOT}" && "{NURL}" {PORT} > data\\nurl.log 2>&1'
    subprocess.Popen(["cmd", "/c", cmd], creationflags=subprocess.CREATE_NO_WINDOW)


def read_url() -> str | None:
    try:
        m = URL_RE.search(URL_LOG.read_text(encoding="utf-8", errors="replace"))
        return m.group(0) if m else None
    except OSError:
        return None


def render_key() -> str | None:
    try:
        k = KEY_FILE.read_text(encoding="utf-8").strip()
        return k or None
    except OSError:
        return None


def sync_render(url: str, key: str) -> None:
    """PATCH YMPX_BASE и триггер деплоя, если адрес поменялся."""
    import httpx
    NET = dict(trust_env=False, proxy=PROXY, timeout=60)
    H = {"Authorization": "Bearer " + key}
    r = httpx.put(f"https://api.render.com/v1/services/{SRV}/env-vars/YMPX_BASE",
                  headers=H, json={"key": "YMPX_BASE", "value": url}, **NET)
    log.info("PATCH YMPX_BASE -> %s", r.status_code)
    if r.status_code < 400:
        d = httpx.post(f"https://api.render.com/v1/services/{SRV}/deploys",
                       headers=H, json={"clearCache": "do_not_clear"}, **NET)
        log.info("POST deploys -> %s", d.status_code)


def main() -> None:
    if not NURL.exists():
        log.error("nurl не найден: %s", NURL)
        return
    known = STATE.read_text(encoding="utf-8").strip() if STATE.exists() else ""
    while True:
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq nurl.exe", "/FO", "CSV"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if "nurl.exe" not in (proc.stdout or ""):
                start_nurl()
                time.sleep(8)
            url = read_url()
            if url and url != known:
                log.info("адрес туннеля: %s (был: %s)", url, known or "-")
                STATE.write_text(url + "\n", encoding="utf-8")
                known = url
                key = render_key()
                if key:
                    sync_render(url, key)
                else:
                    log.warning("ключ Render не найден (%s) — только локальный файл",
                                KEY_FILE)
        except Exception as e:  # noqa: BLE001 — сторож не должен умирать
            log.error("цикл: %s: %s", type(e).__name__, str(e)[:200])
        time.sleep(LOOP_SEC)


if __name__ == "__main__":
    main()
