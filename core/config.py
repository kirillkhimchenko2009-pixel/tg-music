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
    # Билд-метка для сброса кеша мини-аппа: кнопка «Приложение» открывает
    # WEBAPP_URL?v=<APP_BUILD> — Telegram-вебвью не подсовывает старый JS.
    APP_BUILD: str = os.getenv("APP_BUILD", "20261125-webapp")
    ADMIN_IDS: list[int] = _int_list(os.getenv("ADMIN_IDS", ""))
    YM_TOKEN: str = os.getenv("YM_TOKEN", "")
    ZVUK_TOKEN: str = os.getenv("ZVUK_TOKEN", "")
    # Дополнительные токены ЯМ через запятую — резервный пул для устойчивого
    # скачивания (у ЯМ лимиты на аккаунт; второй/третий аккаунт снимает их).
    # Встают ПОСЛЕ своего токена и токенов админов (см. authstore.get_token_pool).
    YM_TOKENS_EXTRA: str = os.getenv("YM_TOKENS_EXTRA", "")
    # Username бота для реферальных ссылок (t.me/<username>?start=ref<uid>);
    # если пусто — берётся из getMe при первом /ref.
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "")
    # Подпись под аудио для вирального привлечения (пересланный трек = реклама);
    # 0 — отключить.
    BRAND_CAPTION: str = os.getenv("BRAND_CAPTION", "1")
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
    # Ещё один русский выход для ЯМ: URL локального сервера в РФ через туннель
    # (nurl — российский туннель, работает с RU-IP). Прод ходит на его
    # /ympx-эндпоинт — быстрее и надёжнее публичного прокси. Пусто = не используется.
    YMPX_BASE: str = os.getenv("YMPX_BASE", "")

    # Сторонние ТГ-боты-источники mp3 (запятая/пробел): "@DeezerMusicBot @spotify_down_bot".
    # Боту приходит «артист — название», его аудио-ответ копируется в чат юзера.
    TG_BOTS: list[str] = [u for u in os.getenv("TG_BOTS", "")
                          .replace(",", " ").replace(";", " ").split() if u]
    TG_BOTS_WAIT: float = float(os.getenv("TG_BOTS_WAIT", "40"))     # сек на ответ бота
    TG_BOTS_COOLDOWN: float = float(os.getenv("TG_BOTS_COOLDOWN", "4"))  # анти-спам между запросами
    DEV_MODE: bool = os.getenv("DEV_MODE", "0") == "1"

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
