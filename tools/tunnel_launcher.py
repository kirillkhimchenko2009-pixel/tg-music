"""Запуск cloudflared quick-tunnel с автоэкспортом URL.

Поднимает cloudflared (конфиг tools/tunnel_edge.yml), парсит новый
адрес trycloudflare из лога и пишет его в data/tunnel_url.txt.
Бот подхватывает файл и сам переустанавливает menu button."""
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL_FILE = ROOT / "data" / "tunnel_url.txt"
CLOUDFLARED = ROOT / "tools" / "cloudflared.exe"
URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

# пререквизит: локальный edge-forwarder уже слушает 8441-8444
# (запускается как: .venv\Scripts\python.exe tools\edge_forwarder.py)


def main() -> None:
    assert CLOUDFLARED.exists(), "cloudflared.exe не найден в tools/"
    cmd = [str(CLOUDFLARED), "tunnel", "--config", "tools/tunnel_edge.yml"]
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    seen = set()
    for line in proc.stdout:  # type: ignore[union-attr]
        sys.stdout.write(line)
        sys.stdout.flush()
        m = URL_RE.search(line)
        if m and m.group(0) not in seen:
            url = m.group(0)
            URL_FILE.parent.mkdir(exist_ok=True)
            URL_FILE.write_text(url, encoding="utf-8")
            print(f"[tunnel_launcher] URL exported -> {url}", flush=True)
            seen.add(url)


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            break
        except Exception as e:  # noqa: BLE001
            print(f"[tunnel_launcher] crash: {e}; restart in 10s", flush=True)
            time.sleep(10)
