"""Permission Checker: единое решение «можно ли скачивать»."""
from __future__ import annotations

import pytest

from music.domain import PermissionInfo, ProviderCapabilities, TrackCandidate
from music.downloader.permission import PermissionChecker, permission_checker
from music.enums import PermissionStatus
from music.errors import NotSupportedError, PermissionDenied, ProviderError


class DummyProvider:
    name = "dummy"
    display_name = "Dummy Source"

    def __init__(self, *, download: bool = True,
                 info: PermissionInfo | None = None,
                 error: Exception | None = None,
                 source_url: str = "https://example.com/track/1",
                 source_error: Exception | None = None):
        self.capabilities = ProviderCapabilities(download=download)
        self._info = info
        self._error = error
        self._source_url = source_url
        self._source_error = source_error

    def supports(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability, False))

    async def get_download_permissions(self, track: TrackCandidate) -> PermissionInfo:
        if self._error is not None:
            raise self._error
        return self._info or PermissionInfo(
            status=PermissionStatus.DOWNLOAD_ALLOWED,
            source_url=self._source_url)

    def get_source_url(self, track: TrackCandidate) -> str:
        if self._source_error is not None:
            raise self._source_error
        return self._source_url


def _track(**kwargs) -> TrackCandidate:
    base = dict(provider="dummy", external_id="1", title="Song",
                artist="Artist", source_url="https://example.com/fallback")
    base.update(kwargs)
    return TrackCandidate(**base)


class TestPermissionChecker:
    async def test_download_allowed(self):
        provider = DummyProvider()
        info = await PermissionChecker().check(provider, _track())
        assert info.status is PermissionStatus.DOWNLOAD_ALLOWED
        assert info.can_download is True

    async def test_source_without_download_capability(self):
        provider = DummyProvider(download=False)
        info = await PermissionChecker().check(provider, _track())
        assert info.status is PermissionStatus.OFFICIAL_LINK_ONLY
        assert info.can_download is False
        assert info.source_url == "https://example.com/track/1"
        assert "не предоставляет файлы" in info.reason

    async def test_not_supported_maps_to_official_link(self):
        provider = DummyProvider(error=NotSupportedError("no"))
        info = await PermissionChecker().check(provider, _track())
        assert info.status is PermissionStatus.OFFICIAL_LINK_ONLY
        assert info.source_url == "https://example.com/track/1"

    async def test_permission_denied_maps_to_blocked(self):
        provider = DummyProvider(error=PermissionDenied("гео-ограничение"))
        info = await PermissionChecker().check(provider, _track())
        assert info.status is PermissionStatus.BLOCKED
        assert "гео-ограничение" in info.reason
        assert info.source_url == "https://example.com/track/1"

    async def test_provider_error_maps_to_license_unknown(self):
        provider = DummyProvider(error=ProviderError("api down"))
        info = await PermissionChecker().check(provider, _track())
        assert info.status is PermissionStatus.LICENSE_UNKNOWN
        assert "Не удалось проверить" in info.reason

    async def test_absolute_error_maps_to_license_unknown(self):
        provider = DummyProvider(error=ValueError("broken json"))
        info = await PermissionChecker().check(provider, _track())
        assert info.status is PermissionStatus.LICENSE_UNKNOWN
        assert "ValueError" in info.reason

    async def test_stream_only_info_keeps_status_and_fills_url(self):
        provider = DummyProvider(info=PermissionInfo(
            status=PermissionStatus.STREAM_ONLY, reason="только стрим"))
        info = await PermissionChecker().check(provider, _track())
        assert info.status is PermissionStatus.STREAM_ONLY
        assert info.can_download is False
        assert info.source_url == "https://example.com/track/1"

    async def test_purchase_info_keeps_purchase_url(self):
        provider = DummyProvider(info=PermissionInfo(
            status=PermissionStatus.PURCHASE_REQUIRED,
            purchase_url="https://shop.example.com/buy"))
        info = await PermissionChecker().check(provider, _track())
        assert info.status is PermissionStatus.PURCHASE_REQUIRED
        assert info.purchase_url == "https://shop.example.com/buy"

    @pytest.mark.parametrize("status", [
        PermissionStatus.STREAM_ONLY, PermissionStatus.OFFICIAL_LINK_ONLY,
        PermissionStatus.AUTH_REQUIRED, PermissionStatus.PURCHASE_REQUIRED,
        PermissionStatus.LICENSE_UNKNOWN, PermissionStatus.BLOCKED,
    ])
    def test_assert_allowed_rejects_everything_else(self, status):
        checker = PermissionChecker()
        info = PermissionInfo(status=status, reason="нельзя")
        with pytest.raises(PermissionDenied) as exc:
            checker.assert_allowed(info)
        assert exc.value.code == status.value

    def test_assert_allowed_passes_download(self):
        PermissionChecker().assert_allowed(
            PermissionInfo(status=PermissionStatus.DOWNLOAD_ALLOWED))

    def test_official_url_falls_back_to_track(self):
        provider = DummyProvider(source_error=RuntimeError("boom"))
        url = PermissionChecker.official_url(provider, _track())
        assert url == "https://example.com/fallback"

    def test_official_url_handles_missing_value(self):
        provider = DummyProvider(source_url="")
        assert PermissionChecker.official_url(
            provider, _track(source_url="")) == ""

    def test_singleton_is_usable(self):
        assert isinstance(permission_checker, PermissionChecker)
