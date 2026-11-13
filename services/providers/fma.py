"""Free Music Archive: публичного API нет (проверено: ни __NEXT_DATA__,
ни трек-ссылок в HTML поиска) → режим OFFICIAL_LINK_ONLY.

Адаптер возвращает честную карточку-ссылку на официальную страницу поиска
FMA — фиктивные результаты не показываются.
"""
from urllib.parse import quote

from services.providers.base import TrackMeta, link_meta

_NAME = "FMA"


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    q = (query or "").strip()
    if not q:
        return []
    # официальная страница поиска FMA (quicksearch — публичный параметр сайта)
    page = "https://freemusicarchive.org/search?quicksearch=" + quote(q)
    return [link_meta("fma", _NAME, q, page)]
