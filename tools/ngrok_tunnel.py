"""Ngrok-туннель со СТАТИЧЕСКИМ доменом (бесплатный тариф ngrok).

Замена ротирующемуся pinggy: mini-app больше не теряет адрес каждые ~55 мин.
1. Пользователь резервирует бесплатный статический домен в дашборде ngrok
   (Dashboard → Domains → Create), копирует authtoken.
2. В .env: NGROK_AUTHTOKEN=... и NGROK_DOMAIN=xxx.ngrok-free.app
3. Скрипт поднимает туннель на локальный 127.0.0.1:8000 и пишет URL в
   data/tunnel_url.txt — бот сам перевешивает кнопку меню (webapp_state).

Скрипт живёт бесконечно: при обрыве пересогласовывает туннель с бэкоффом.
Требування: pip install pyngrok (бинарник ngrok скачается сам в первый запуск).
Не забыть остановить pinggy-туннель, иначе два писателя будут перетирать URL.
"""
import logging
import sys
import time
from pathlib import Path

# импорт наших модулей требует корень репозитория в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyngrok import ngrok as pg

from core.config import settings
from core.single_instance import acquire

URL_FILE = Path(__file__).resolve().parent.parent / "data" / "tunnel_url.txt"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] ngrok: %(message)s")
log = logging.getLogger("ngrok")


def write_url(url: str) -> None:
    URL_FILE.write_text(url.strip() + "\n", encoding="utf-8")
    log.info("URL записан: %s", url)


def main() -> None:
    token = settings.NGROK_AUTHTOKEN
    domain = settings.NGROK_DOMAIN
    if not token or not domain:
        log.error("Нет NGROK_AUTHTOKEN/NGROK_DOMAIN в .env — заполни и перезапусти")
        return
    try:
        pg.set_auth_token(token)
    except Exception as e:  # noqa: BLE001
        if "already set" in str(e):
            log.info("authtoken уже в конфиге — ок")
        else:
            raise
    backoff = 5
    while True:
        tunnel = None
        try:
            tunnel = pg.connect("127.0.0.1:8000",
                                proto="http",
                                domain=domain,
                                bind_tls=True,
                                )
            public_url = tunnel.public_url.replace("http://", "https://")
            log.info("туннель поднят: %s", public_url)
            write_url(public_url)
            backoff = 5
            # спим в цикле, следя за живостью туннеля
            while True:
                time.sleep(20)
                alive = [t for t in pg.get_tunnels()
                         if getattr(t, "name", "") == "http-8000"]
                if not alive:
                    log.warning("туннель пропал — пересоздаю")
                    break
        except KeyboardInterrupt:
            return
        except Exception as e:  # noqa: BLE001
            log.warning("ошибка туннеля: %s — пауза %d с", str(e)[:120], backoff)
        finally:
            try:
                if tunnel:
                    pg.disconnect(tunnel.public_url)
            except Exception:  # noqa: BLE001
                pass
        time.sleep(backoff)
        backoff = min(backoff * 2, 120)


if __name__ == "__main__":
    acquire("tunnel_ngrok")
    main()
