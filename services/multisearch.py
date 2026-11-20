"""Комбинированный поиск по единому реестру источников (мастер-промпт §4).

Один прогон поиска параллельно опрашивает все источники выбранной области
(scope: all / ru / intl / downloadable / official_links). Каждый адаптер
ограничен своим таймаутом и выполняется независимо: ошибка одного источника
НЕ останавливает весь поиск (мастер-промпт §4).

Выдача сливается round-robin по источникам — в первых результатах
представлены все площадки, а не только самая быстрая.
"""
import asyncio
import logging

from services import registry
from services.providers.base import TrackMeta

log = logging.getLogger("providers.multisearch")

# таймауты per-provider (ytmusic медленный: ytmusicapi может падать в
# TimeoutError и переключаться на yt-dlp — даём больше)
_WAIT = {"ytmusic": 13.0}


def _module_adapter(mod_name: str):
    """async search(query, limit) модульного адаптера (лениво, при выкате)."""
    async def _run(q: str, limit: int, token: str | None):
        import importlib
        mod = importlib.import_module(f"services.providers.{mod_name}")
        return await mod.search(q, limit)
    return _run


async def _yandex(q: str, limit: int, token: str | None):
    if not token:
        return []
    from services.providers.yandex import YandexProvider
    return await YandexProvider(token).search(q, limit)


async def _zvuk(q: str, limit: int, token: str | None):
    from services.providers import zvuk
    return await zvuk.search(q, limit, token=token)


async def _ytmusic(q: str, limit: int, token: str | None):
    from services.providers.youtube import YouTubeProvider
    return await YouTubeProvider().search(q, limit)


# provider_id → адаптер; источники без поискового адаптера тут отсутствуют
# (spotify/bandcamp/user_upload — импорт плейлистов и загрузка, не поиск)
_ADAPTERS = {
    "zaycev": _module_adapter("zaycev"),
    "soundcloud": _module_adapter("soundcloud"),
    "hotplayer": _module_adapter("hotplayer"),
    "mp3party": _module_adapter("mp3party"),
    "hitmoz": _module_adapter("hitmoz"),
    "promodj": _module_adapter("promodj"),
    "lisen": _module_adapter("lisen"),
    "vk": _module_adapter("vk"),
    "mts": _module_adapter("mts"),
    "audius": _module_adapter("audius"),
    "ccmixter": _module_adapter("ccmixter"),
    "fma": _module_adapter("fma"),
    "yandex": _yandex,
    "zvuk": _zvuk,
    "ytmusic": _ytmusic,
}


def _interleave(by_src: dict[str, list[TrackMeta]]) -> list[TrackMeta]:
    """Round-robin по источникам в порядке реестра: 1-й результат каждого
    источника, затем 2-й и т.д."""
    order = [s.provider_id for s in registry.specs("all")]
    keys = [k for k in order if by_src.get(k)]
    # источники вне реестра (на всякий случай) — в конец
    keys += [k for k in by_src if k not in keys]
    out: list[TrackMeta] = []
    i = 0
    while True:
        pushed = False
        for k in keys:
            arr = by_src[k]
            if i < len(arr):
                out.append(arr[i])
                pushed = True
        if not pushed:
            break
        i += 1
    return out


async def web_search(q: str, limit: int = 24, scope: str = "all",
                     tokens: dict[str, str | None] | None = None,
                     per_wait: dict[str, float] | None = None) -> list[TrackMeta]:
    """Параллельный поиск по всем источникам области.

    tokens: {"ym": ..., "zvuk": ...} — личные токены пользователя (AUTH_REQUIRED).
    Ошибки и таймауты изолированы: провайдер без результата просто пропадает.
    """
    q = (q or "").strip()
    if not q:
        return []
    tokens = tokens or {}
    waits = dict(_WAIT)
    if per_wait:
        waits.update(per_wait)

    by_src: dict[str, list[TrackMeta]] = {}
    pending: list[tuple[str, asyncio.Task[list[TrackMeta]]]] = []
    for spec in registry.specs(scope):
        adapter = _ADAPTERS.get(spec.provider_id)
        if adapter is None:
            continue
        wait = waits.get(spec.provider_id, 9.0)
        # токены хранятся под ключом authstore: "ym" для Яндекса, иначе source
        tok = tokens.get("ym" if spec.provider_id == "yandex" else spec.provider_id)

        async def _one(aid=spec.provider_id, fn=adapter, w=wait, t=tok):
            try:
                res = await asyncio.wait_for(fn(q, limit, t), w)
                registry.health_mark(aid, True)
                return res or []
            except Exception as e:
                registry.health_mark(aid, False, f"{type(e).__name__}: {e}")
                log.info("%s: поиск не удался (%s)", aid, type(e).__name__)
                return []

        pending.append((spec.provider_id, asyncio.create_task(_one())))

    # Все независимые источники запускаются вместе: время ответа ограничено
    # самым медленным таймаутом, а не суммой таймаутов провайдеров.
    if pending:
        results = await asyncio.gather(*(task for _, task in pending),
                                       return_exceptions=True)
        for (provider_id, _), result in zip(pending, results):
            if isinstance(result, Exception):  # дополнительная изоляция адаптера
                log.info("%s: изоляция ошибки (%s)", provider_id,
                         type(result).__name__)
                continue
            if result:
                by_src[provider_id] = result

    merged: list[TrackMeta] = []
    seen: set[tuple[str, str]] = set()
    for m in _interleave(by_src):
        # «блюр»-заливки (18+ у ЯМ — слова замазаны/запиканы) в поиск не
        # показываем: по решению юзера такое не играет и не скачивается
        if getattr(m, "explicit", False):
            continue
        k = m.key()
        if k in seen:
            continue
        seen.add(k)
        merged.append(m)
        if len(merged) >= limit * 2:      # запас на дедуп при upsert
            break
    return merged


def scopes_for(meta: TrackMeta) -> list[str]:
    """Какие области поиска содержат этот трек (для кнопок-фильтров бота)."""
    spec = registry.get(meta.source)
    if not spec:
        return ["all"]
    out = ["all"]
    if spec.provider_country == "RU":
        out.append("ru")
    elif spec.provider_country == "GLOBAL":
        out.append("intl")
    if spec.supports_download and spec.download_permission_mode == registry.ProviderMode.DOWNLOAD_ALLOWED:
        out.append("downloadable")
    if spec.download_permission_mode == registry.ProviderMode.OFFICIAL_LINK_ONLY and spec.supports_official_link:
        out.append("official_links")
    return out
