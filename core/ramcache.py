"""Простейший RAM-кэш ответов (TTL) для горячих GET-ручек.

Neon + 0.1 CPU на Render free: каждый запрос волны/коллекции делает
десятки БД-раунтрипов и отвечает 1-6 сек. Чтения слабо меняются за
минуты — кешируем ответ целиком, инвалидация по uid при мутациях.
"""
import time

_cache: dict[tuple, tuple[float, object]] = {}
MAX_ENTRIES = 4096


def get(key: tuple):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def put(key: tuple, val, ttl: float) -> None:
    if len(_cache) > MAX_ENTRIES:
        now = time.time()
        for k in [k for k, v in _cache.items() if v[0] <= now]:
            _cache.pop(k, None)
        if len(_cache) > MAX_ENTRIES:
            _cache.clear()
    _cache[key] = (time.time() + ttl, val)


def drop_uid(uid: int) -> None:
    """Инвалидация всех кешей пользователя (после мутаций)."""
    for k in [k for k in _cache if len(k) > 1 and k[1] == uid]:
        _cache.pop(k, None)
