"""МТС Музыка: официального публичного API нет (сайт — Next.js-оболочка) →
режим OFFICIAL_LINK_ONLY (мастер-промпт §RUSSIAN SOURCE 7).

Адаптер не обходит авторизацию/подписку — возвращает карточку-ссылку на
официальную страницу поиска music.mts.ru/search?q=.
"""
from urllib.parse import quote

from services.providers.base import TrackMeta, link_meta

_NAME = "МТС Музыка"


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    q = (query or "").strip()
    if not q:
        return []
    page = "https://music.mts.ru/search?q=" + quote(q)
    return [link_meta("mts", _NAME, q, page)]
