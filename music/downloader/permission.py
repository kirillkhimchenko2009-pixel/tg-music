"""Permission Checker: единая точка решения «можно ли скачивать»."""
from __future__ import annotations

import logging

from ..domain import PermissionInfo, TrackCandidate
from ..enums import PermissionStatus
from ..errors import NotSupportedError, PermissionDenied, ProviderError

log = logging.getLogger("music.permission")


class PermissionChecker:
    """Работает только с DOWNLOAD_ALLOWED; всё остальное — официальная ссылка."""

    async def check(self, provider, track: TrackCandidate) -> PermissionInfo:
        if not provider.supports("download"):
            return PermissionInfo(
                status=PermissionStatus.OFFICIAL_LINK_ONLY,
                reason=f"{provider.display_name}: источник не предоставляет файлы",
                source_url=self.official_url(provider, track),
                license_name=track.license_name, license_url=track.license_url)
        try:
            info = await provider.get_download_permissions(track)
        except NotSupportedError:
            return PermissionInfo(
                status=PermissionStatus.OFFICIAL_LINK_ONLY,
                reason=f"{provider.display_name}: скачивание не поддерживается",
                source_url=self.official_url(provider, track))
        except PermissionDenied as exc:
            return PermissionInfo(
                status=PermissionStatus.BLOCKED, reason=str(exc),
                source_url=self.official_url(provider, track))
        except Exception as exc:  # noqa: BLE001
            log.info("permission %s/%s: %s", provider.name, track.external_id, exc)
            return PermissionInfo(
                status=PermissionStatus.LICENSE_UNKNOWN,
                reason=f"Не удалось проверить разрешение: {type(exc).__name__}",
                source_url=self.official_url(provider, track))

        if info.status is PermissionStatus.DOWNLOAD_ALLOWED:
            # адаптер мог «разрешить» без capability — перестраховка
            if not provider.supports("download"):
                info.status = PermissionStatus.OFFICIAL_LINK_ONLY
                info.reason = "Источник не поддерживает скачивание"
        elif not info.source_url:
            info.source_url = self.official_url(provider, track)
        return info

    def assert_allowed(self, info: PermissionInfo, case_id: str = "") -> None:
        if info.status is not PermissionStatus.DOWNLOAD_ALLOWED:
            raise PermissionDenied(
                info.reason or f"Скачивание запрещено ({info.status.value})",
                code=info.status.value)

    @staticmethod
    def official_url(provider, track: TrackCandidate) -> str:
        try:
            return provider.get_source_url(track) or track.source_url or ""
        except Exception:  # noqa: BLE001
            return track.source_url or ""


permission_checker = PermissionChecker()

__all__ = ["PermissionChecker", "permission_checker"]
