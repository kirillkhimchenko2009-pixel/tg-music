"""Единый реестр музыкальных источников (мастер-промпт §3).

12 канонических источников (5 международных + 7 российских) + дополнительные
экстра-источники, которые уже были в системе — ничего не удаляется, все
подключены к одной выдаче.

Каждый источник описан ProviderSpec'ом: флаги возможностей + api_status +
download_permission_mode — режим передачи файлов пользователю.

Ключевое правило per-track: DOWNLOAD_ALLOWED в реестре — лишь потенциал
источника; реальное разрешение проверяет адаптер для КОНКРЕТНОЙ композиции
(TrackMeta.downloadable), а не «всему сайту» (мастер-промпт §3).
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass

log = logging.getLogger("providers.registry")


class ProviderMode(str, enum.Enum):
    """Режим передачи файлов (мастер-промпт §3)."""
    DOWNLOAD_ALLOWED = "DOWNLOAD_ALLOWED"                 # передача разрешена (per-track проверка)
    OFFICIAL_LINK_ONLY = "OFFICIAL_LINK_ONLY"             # только официальная ссылка
    AUTH_REQUIRED = "AUTH_REQUIRED"                       # работает по личному токену пользователя
    PARTNER_PERMISSION_REQUIRED = "PARTNER_PERMISSION_REQUIRED"
    LICENSE_UNKNOWN = "LICENSE_UNKNOWN"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str                       # source в Track.source
    provider_name: str                     # человекочитаемое имя для UI
    provider_country: str                  # "RU" | "GLOBAL"
    provider_language: str                 # "ru" | "en" | "any"
    provider_status: str = "active"        # active | experimental | disabled
    supports_search: bool = True
    supports_metadata: bool = True
    supports_cover: bool = True
    supports_download: bool = False        # источник вообще может отдать файл
    supports_official_link: bool = True
    supports_license_check: bool = False   # умеет назвать лицензию конкретного файла
    supports_original_quality: bool = False
    api_status: str = "official_api"       # official_api | open_html | html_only | none | sdk_token
    download_permission_mode: ProviderMode = ProviderMode.OFFICIAL_LINK_ONLY
    extra: bool = False                    # вне 12 канонических (наследие — не удаляем)


# ---------------------------------------------------------------- 12 канонических
REGISTRY: list[ProviderSpec] = [
    # --- международные (5) ---
    ProviderSpec("audius", "Audius", "GLOBAL", "en",
                 supports_download=True, supports_license_check=False,
                 supports_original_quality=True, api_status="official_api",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("jamendo", "Jamendo", "GLOBAL", "en",
                 supports_download=True, supports_license_check=True,
                 supports_original_quality=True, api_status="official_api",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("archive", "Internet Archive", "GLOBAL", "any",
                 supports_download=True, supports_license_check=True,
                 supports_original_quality=True, api_status="official_api",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("ccmixter", "ccMixter", "GLOBAL", "en",
                 supports_cover=False, supports_download=True,
                 supports_license_check=True,
                 supports_original_quality=True, api_status="official_api",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("fma", "Free Music Archive", "GLOBAL", "en",
                 supports_metadata=False, supports_cover=False,
                 supports_download=False, supports_official_link=True,
                 api_status="none",
                 download_permission_mode=ProviderMode.OFFICIAL_LINK_ONLY),
    # --- российские (7) ---
    ProviderSpec("zaycev", "Zaycev.net", "RU", "ru",
                 supports_download=True, supports_license_check=False,
                 supports_original_quality=True, api_status="open_html",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("promodj", "PromoDJ", "RU", "ru",
                 supports_download=True, supports_license_check=False,
                 supports_original_quality=True, api_status="open_html",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("lisen", "Lisen.to", "RU", "ru",
                 supports_metadata=False, supports_cover=False,
                 supports_download=False, api_status="html_only",
                 download_permission_mode=ProviderMode.OFFICIAL_LINK_ONLY),
    ProviderSpec("vk", "VK Музыка", "RU", "ru",
                 supports_metadata=False, supports_cover=False,
                 supports_download=False, api_status="html_only",
                 download_permission_mode=ProviderMode.OFFICIAL_LINK_ONLY),
    ProviderSpec("yandex", "Яндекс Музыка", "RU", "ru",
                 supports_download=True, supports_official_link=True,
                 api_status="sdk_token",
                 download_permission_mode=ProviderMode.AUTH_REQUIRED),
    ProviderSpec("zvuk", "Звук", "RU", "ru",
                 supports_download=True, supports_official_link=True,
                 api_status="sdk_token",
                 download_permission_mode=ProviderMode.AUTH_REQUIRED),
    ProviderSpec("mts", "МТС Музыка", "RU", "ru",
                 supports_metadata=False, supports_cover=False,
                 supports_download=False, api_status="html_only",
                 download_permission_mode=ProviderMode.OFFICIAL_LINK_ONLY),
    # --- экстра-источники (наследие, остаются в «все источники») ---
    ProviderSpec("ytmusic", "YouTube Music", "GLOBAL", "any", extra=True,
                 supports_download=True, supports_official_link=True,
                 api_status="open_html",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("soundcloud", "SoundCloud", "GLOBAL", "any", extra=True,
                 supports_download=True, api_status="official_api",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("hotplayer", "Hotplayer", "GLOBAL", "any", extra=True,
                 supports_download=True, api_status="open_html",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("mp3party", "MP3party", "RU", "ru", extra=True,
                 supports_download=True, api_status="open_html",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("hitmoz", "Hitmoz", "RU", "ru", extra=True,
                 supports_download=True, api_status="open_html",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("spotify", "Spotify", "GLOBAL", "any", extra=True,
                 supports_search=False, supports_download=False,
                 api_status="official_api",
                 download_permission_mode=ProviderMode.PARTNER_PERMISSION_REQUIRED),
    ProviderSpec("bandcamp", "Bandcamp", "GLOBAL", "any", extra=True,
                 supports_search=False, supports_download=True,
                 api_status="open_html",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
    ProviderSpec("user_upload", "Загрузка пользователя", "GLOBAL", "any", extra=True,
                 supports_search=False, supports_download=True,
                 supports_official_link=False,
                 api_status="none",
                 download_permission_mode=ProviderMode.DOWNLOAD_ALLOWED),
]

_BY_ID: dict[str, ProviderSpec] = {s.provider_id: s for s in REGISTRY}

# --- health: last_health_check живёт вне замороженного spec ---
_HEALTH: dict[str, dict] = {}


def get(provider_id: str) -> ProviderSpec | None:
    return _BY_ID.get(provider_id)


def mode_of(provider_id: str) -> ProviderMode:
    s = _BY_ID.get(provider_id)
    return s.download_permission_mode if s else ProviderMode.LICENSE_UNKNOWN


def specs(scope: str = "all") -> list[ProviderSpec]:
    """Фильтр по области поиска (мастер-промпт §5):
    all | ru | intl | downloadable | official_links"""
    out: list[ProviderSpec] = []
    for s in REGISTRY:
        if s.provider_status != "active":
            continue
        if scope == "ru" and s.provider_country != "RU":
            continue
        if scope == "intl" and s.provider_country != "GLOBAL":
            continue
        if scope == "downloadable" and not (
                s.supports_download and s.download_permission_mode == ProviderMode.DOWNLOAD_ALLOWED):
            continue
        if scope == "official_links" and (
                s.download_permission_mode != ProviderMode.OFFICIAL_LINK_ONLY
                or not s.supports_official_link):
            continue
        out.append(s)
    return out


def ids(scope: str = "all") -> list[str]:
    return [s.provider_id for s in specs(scope)]


def health_mark(provider_id: str, ok: bool, error: str = "") -> None:
    """Фиксация результата обращения к источнику (api_status, last_health_check)."""
    _HEALTH[provider_id] = {
        "ok": ok,
        "error": error[:200],
        "last_health_check": time.time(),
    }


def health_all() -> dict[str, dict]:
    return {pid: dict(v) for pid, v in _HEALTH.items()}
