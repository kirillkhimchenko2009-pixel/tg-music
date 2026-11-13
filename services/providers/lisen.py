"""Lisen.to: поиск — JS-приложение (search-app) без публичного API
(проверено: в бандлах только bootstrap, GET-маршрутов поиска нет) →
режим OFFICIAL_LINK_ONLY (мастер-промпт §RUSSIAN SOURCE 3).

Адаптер возвращает карточку-ссылку на официальный сайт; ограничения сайта
не обходятся, фиктивные результаты не показываются. Лицензия конкретной
композиции проверяется пользователем на официальной странице.
"""
from services.providers.base import TrackMeta, link_meta

_NAME = "Lisen.to"


async def search(query: str, limit: int = 6) -> list[TrackMeta]:
    q = (query or "").strip()
    if not q:
        return []
    # GET-маршрута поиска нет — ведём на официальный сайт (поиск включается
    # на странице); как только появится публичный API, режим обновится
    return [link_meta("lisen", _NAME, q, "https://lisen.to/ru")]
