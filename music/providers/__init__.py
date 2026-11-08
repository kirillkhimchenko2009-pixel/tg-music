"""Провайдеры музыкальных источников и реестр."""
from .base import MusicProvider, UnsupportedProvider
from .registry import (ProviderRegistry, build_default_registry, enabled_providers,
                       get_provider, provider_specs, registry)

__all__ = [
    "MusicProvider", "UnsupportedProvider", "ProviderRegistry",
    "build_default_registry", "enabled_providers", "get_provider",
    "provider_specs", "registry",
]
