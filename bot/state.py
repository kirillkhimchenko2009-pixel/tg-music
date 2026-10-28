"""Лёгкое состояние бота в памяти."""
# user_id -> {"query": str, "ids": [...], "page": int}
PENDING_SEARCH: dict[int, dict] = {}
# user_id -> {"title": str, "metas": [TrackMeta...]}
EXT_CACHE: dict[int, dict] = {}
# user_id -> {"track_id": int} ожидание названия нового плейлиста
pending_new_playlist: dict[int, dict] = {}

def search_cache() -> dict[int, dict]:
    return PENDING_SEARCH
