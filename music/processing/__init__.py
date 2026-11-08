"""Обработка аудио и метаданных."""
from .converter import (audio_format, convert, convert_for_telegram,
                        is_telegram_audio, media_kind_for, mime_for,
                        telegram_filename)
from .cover_processor import (best_cover_bytes, fetch_cover, make_background,
                              make_thumbnail, prepare_cover, store_cover_copy,
                              upgrade_cover_url)
from .metadata_editor import (MetadataEditResult, MetadataFields, embedded_cover,
                              read_metadata, write_metadata)

__all__ = [
    "audio_format", "convert", "convert_for_telegram", "is_telegram_audio",
    "media_kind_for", "mime_for", "telegram_filename", "best_cover_bytes",
    "fetch_cover", "make_background", "make_thumbnail", "prepare_cover",
    "upgrade_cover_url", "store_cover_copy", "MetadataEditResult",
    "MetadataFields", "embedded_cover", "read_metadata", "write_metadata",
]
