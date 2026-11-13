"""VK Музыка: закрытого музыкального API и официальной интеграции нет →
режим OFFICIAL_LINK_ONLY (мастер-промпт §RUSSIAN SOURCE 4).

Адаптер НЕ трогает внутренний API приложения, cookies пользователей и
защищённые аудиопотоки — возвращает карточку-ссылку на официальный поиск
VK (vk.com/audio?q=). При появлении официального разрешения на передачу
конкретных композиций включается отдельный DOWNLOAD_ALLOWED с проверкой прав.
"""
from urllib.parse import quote

from services.providers.base import TrackMeta, link_meta

_NAME = "VK Музыка"


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    q = (query or "").strip()
    if not q:
        return []
    page = "https://vk.com/audio?q=" + quote(q)
    return [link_meta("vk", _NAME, q, page)]
