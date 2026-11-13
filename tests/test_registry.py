"""Единый реестр источников (мастер-промпт §3): 12 канонических + экстра,
флаги возможностей, режимы передачи, области поиска, health."""
from __future__ import annotations

import time

from services import registry
from services.registry import ProviderMode, ProviderSpec

CANONICAL_12 = {"audius", "jamendo", "archive", "ccmixter", "fma",
                "zaycev", "promodj", "lisen", "vk", "yandex", "zvuk", "mts"}
RU_CANONICAL = {"zaycev", "promodj", "lisen", "vk", "yandex", "zvuk", "mts"}


class TestCanonicalTwelve:
    def test_all_12_canonical_present(self):
        ids = {s.provider_id for s in registry.REGISTRY if not s.extra}
        assert CANONICAL_12 <= ids

    def test_ru_seven_canonical_are_ru(self):
        for pid in RU_CANONICAL:
            spec = registry.get(pid)
            assert spec is not None and spec.provider_country == "RU"

    def test_international_five_are_global(self):
        for pid in ("audius", "jamendo", "archive", "ccmixter", "fma"):
            assert registry.get(pid).provider_country == "GLOBAL"

    def test_extras_do_not_replace_canonical(self):
        """Экстра-источники (наследие) не вытесняют 12 канонических."""
        extras = {s.provider_id for s in registry.REGISTRY if s.extra}
        assert {"soundcloud", "hotplayer", "mp3party", "hitmoz", "ytmusic"} <= extras
        assert not (extras & CANONICAL_12)


class TestSpecFields:
    """Каждый источник описан всеми полями мастер-промпта §3."""

    def test_every_spec_has_all_fields(self):
        for s in registry.REGISTRY:
            assert s.provider_id and s.provider_name
            assert s.provider_country in ("RU", "GLOBAL")
            assert s.provider_language
            assert s.provider_status
            assert isinstance(s.supports_search, bool)
            assert isinstance(s.supports_metadata, bool)
            assert isinstance(s.supports_cover, bool)
            assert isinstance(s.supports_download, bool)
            assert isinstance(s.supports_official_link, bool)
            assert isinstance(s.supports_license_check, bool)
            assert isinstance(s.supports_original_quality, bool)
            assert s.api_status in ("official_api", "open_html", "html_only",
                                    "none", "sdk_token")
            assert isinstance(s.download_permission_mode, ProviderMode)

    def test_all_modes_of_master_prompt_exist(self):
        for m in ("DOWNLOAD_ALLOWED", "OFFICIAL_LINK_ONLY", "AUTH_REQUIRED",
                  "PARTNER_PERMISSION_REQUIRED", "LICENSE_UNKNOWN",
                  "TEMPORARILY_UNAVAILABLE", "BLOCKED"):
            assert ProviderMode(m) is not None

    def test_unknown_source_returns_none(self):
        assert registry.get("nonexistent") is None
        assert registry.mode_of("nonexistent") == ProviderMode.LICENSE_UNKNOWN


class TestPermissionModes:
    """Per-track правило: DOWNLOAD_ALLOWED никогда не присваивается «всему сайту»
    без per-track проверки адаптером."""

    def test_free_download_sources(self):
        for pid in ("audius", "jamendo", "archive", "ccmixter", "zaycev", "promodj"):
            spec = registry.get(pid)
            assert spec.download_permission_mode == ProviderMode.DOWNLOAD_ALLOWED
            assert spec.supports_download is True

    def test_official_link_only_sources(self):
        for pid in ("vk", "mts", "lisen", "fma"):
            spec = registry.get(pid)
            assert spec.download_permission_mode == ProviderMode.OFFICIAL_LINK_ONLY
            assert spec.supports_download is False
            assert spec.supports_official_link is True

    def test_auth_required_sources(self):
        for pid in ("yandex", "zvuk"):
            assert registry.get(pid).download_permission_mode == \
                ProviderMode.AUTH_REQUIRED

    def test_no_api_sources_are_honest(self):
        """Источники без API не заявляют метаданные/обложки/поиск."""
        for pid in ("fma", "vk", "mts", "lisen"):
            spec = registry.get(pid)
            assert spec.api_status in ("none", "html_only")
            assert spec.supports_metadata is False
            assert spec.supports_cover is False


class TestScopes:
    def test_scope_ru(self):
        ids = set(registry.ids("ru"))
        assert RU_CANONICAL <= ids
        assert "audius" not in ids and "fma" not in ids

    def test_scope_intl(self):
        ids = set(registry.ids("intl"))
        assert {"audius", "jamendo", "archive", "ccmixter", "fma"} <= ids
        assert not (ids & RU_CANONICAL)

    def test_scope_downloadable(self):
        for s in registry.specs("downloadable"):
            assert s.supports_download and \
                s.download_permission_mode == ProviderMode.DOWNLOAD_ALLOWED
        assert "vk" not in registry.ids("downloadable")

    def test_scope_official_links(self):
        assert set(registry.ids("official_links")) == {"fma", "lisen", "vk", "mts"}

    def test_scope_all_contains_everything_active(self):
        ids = registry.ids("all")
        assert CANONICAL_12 <= set(ids)
        assert len(ids) == len(registry.specs("all"))

    def test_unknown_scope_falls_back_to_all(self):
        assert set(registry.ids("bloob")) == set(registry.ids("all"))

    def test_disabled_provider_excluded(self):
        s = registry.specs("all")
        assert all(x.provider_status == "active" for x in s)


class TestHealth:
    def test_health_mark_ok_and_error(self):
        registry.health_mark("promodj", True)
        registry.health_mark("vk", False, error="HTTP 403: недоступен")
        h = registry.health_all()
        assert h["promodj"]["ok"] is True
        assert h["vk"]["ok"] is False
        assert "403" in h["vk"]["error"]
        assert h["vk"]["last_health_check"] <= time.time() + 1

    def test_health_error_truncated(self):
        registry.health_mark("zaycev", False, error="x" * 500)
        assert len(registry.health_all()["zaycev"]["error"]) <= 200
