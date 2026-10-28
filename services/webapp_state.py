"""Автосинхронизация URL мини-аппа: следит за data/tunnel_url.txt и
перерегистрирует menu button, когда туннель (любой) перезапускается
и адрес меняется. Так URL из trycloudflare/pinggy больше не протухает
в меню Telegram — бот сам ставит актуальную кнопку."""
import asyncio
import logging
from pathlib import Path

from core.config import settings

log = logging.getLogger(__name__)

URL_FILE = Path(__file__).resolve().parent.parent / "data" / "tunnel_url.txt"
CHECK_EVERY = 30  # секунд


def read_tunnel_url() -> str | None:
    """Свежий URL из файла туннеля (если файл не старше 24 ч)."""
    try:
        if not URL_FILE.exists():
            return None
        import time
        if time.time() - URL_FILE.stat().st_mtime > 24 * 3600:
            return None
        url = URL_FILE.read_text(encoding="utf-8-sig").strip()
        return url if url.startswith("https://") else None
    except Exception as e:  # noqa: BLE001
        log.debug("tunnel_url read failed: %s", e)
        return None


async def _apply_url(bot, url: str) -> None:
    settings.WEBAPP_URL = url
    from bot.main import register_menu
    await register_menu(bot)
    log.info("Мини-апп URL обновлён: %s", url)


def read_full_webapp_url() -> str | None:
    """Полный URL мини-аппа из data/webapp_url.txt (может быть GH Pages + ?api=).
    Учитывается только свежий файл (<24 ч по mtime)."""
    path = URL_FILE.parent / "webapp_url.txt"
    try:
        if not path.exists():
            return None
        import time
        if time.time() - path.stat().st_mtime > 24 * 3600:
            return None
        url = path.read_text(encoding="utf-8-sig").strip()
        return url if url.startswith("https://") else None
    except Exception as e:  # noqa: BLE001
        log.debug("webapp_url read failed: %s", e)
        return None


async def watch_webapp_url(bot) -> None:
    """Фоновая задача: применяет URL из tunnel_url.txt при его смене."""
    last = None
    while True:
        try:
            url = read_full_webapp_url() or read_tunnel_url()
            if url and url != last:
                await _apply_url(bot, url)
                last = url
        except Exception as e:  # noqa: BLE001
            log.warning("webapp url watcher: %s", e)
        await asyncio.sleep(CHECK_EVERY)
