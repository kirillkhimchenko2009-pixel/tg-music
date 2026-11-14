"""Конфиг приложения. Читает .env из корня проекта."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def _int_list(s: str) -> list[int]:
    return [int(x) for x in s.replace(";", ",").split(",") if x.strip()]

class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "")
    ADMIN_IDS: list[int] = _int_list(os.getenv("ADMIN_IDS", ""))
    YM_TOKEN: str = os.getenv("YM_TOKEN", "")
    ZVUK_TOKEN: str = os.getenv("ZVUK_TOKEN", "")
    SPOTIFY_CLIENT_ID: str = os.getenv("SPOTIFY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET: str = os.getenv("SPOTIFY_CLIENT_SECRET", "")
    DEFAULT_QUALITY: str = os.getenv("DEFAULT_QUALITY", "aac320")
    PROXY_URL: str = os.getenv("PROXY_URL", "")   # прокси для YouTube/Spotify и вообще
    _raw_bot_proxy = os.getenv("BOT_PROXY_URL")
    # пустая строка = «явно без прокси»; None (не задан) → общий PROXY_URL
    BOT_PROXY_URL: str = (_raw_bot_proxy if _raw_bot_proxy is not None
                          else os.getenv("PROXY_URL", ""))  # для aiogram/Telegram API

    # Исключение (решение юзера): русский прокси ТОЛЬКО для анонимных запросов
    # Яндекс.Музыки (импорт плейлистов без токена) — ЯМ гео-блокирует
    # зарубежные IP (HTTP 451). Остальной трафик не трогаем.
    YM_PROXY_URL: str = os.getenv("YM_PROXY_URL", "")

    # Сторонние ТГ-боты-источники mp3 (запятая/пробел): "@DeezerMusicBot @spotify_down_bot".
    # Боту приходит «артист — название», его аудио-ответ копируется в чат юзера.
    TG_BOTS: list[str] = [u for u in os.getenv("TG_BOTS", "")
                          .replace(",", " ").replace(";", " ").split() if u]
    TG_BOTS_WAIT: float = float(os.getenv("TG_BOTS_WAIT", "40"))     # сек на ответ бота
    TG_BOTS_COOLDOWN: float = float(os.getenv("TG_BOTS_COOLDOWN", "4"))  # анти-спам между запросами
    DEV_MODE: bool = os.getenv("DEV_MODE", "0") == "1"
    NGROK_AUTHTOKEN: str = os.getenv("NGROK_AUTHTOKEN", "")
    NGROK_DOMAIN: str = os.getenv("NGROK_DOMAIN", "")

    BOT_API_URL: str = os.getenv("BOT_API_URL", "")  # локальный Bot API сервер (для файлов >50 МБ)

    YT_PROBE_TIMEOUT: float = float(os.getenv("YT_PROBE_TIMEOUT", "6"))

    DATA_DIR: Path = ROOT / "data"
    DB_PATH: Path = DATA_DIR / "music.db"
    COVERS_DIR: Path = DATA_DIR / "covers"
    AUDIO_DIR: Path = DATA_DIR / "audio"
    TEMP_DIR: Path = DATA_DIR / "temp"

    def __init__(self):
        for d in (self.DATA_DIR, self.COVERS_DIR, self.AUDIO_DIR, self.TEMP_DIR):
            d.mkdir(parents=True, exist_ok=True)

settings = Settings()
