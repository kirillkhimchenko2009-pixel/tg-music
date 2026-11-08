"""Отправка треков в Telegram: sendAudio для MP3/M4A, sendDocument для остальных.

Кэш `file_id` живёт в `music_cached_files`: повторная отправка того же трека
не требует ни скачивания, ни повторной загрузки файла в Telegram.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from urllib.parse import urlsplit

from aiogram import types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, FSInputFile

from ..cache.music_cache import set_telegram_file
from ..config import music_settings
from ..db import AsyncSessionLocal
from ..domain import MediaKind, PreparedTrack, fmt_duration
from ..enums import QualityCategory
from ..processing.converter import telegram_filename
from ..processing.cover_processor import make_thumbnail
from ..storage import cleanup_job_dir
from .. import repository

log = logging.getLogger("music.telegram.sender")


def safe_url(url: str | None) -> str:
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return url or ""


def _clip(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def build_caption(prepared: PreparedTrack) -> str:
    candidate = prepared.candidate
    artist = candidate.artist or "Неизвестный исполнитель"
    title = candidate.title or "Без названия"
    lines = [f"🎵 <b>{html.escape(_clip(artist, 120))} — "
             f"{html.escape(_clip(title, 160))}</b>"]

    album_line = ""
    if candidate.album:
        album_line = html.escape(_clip(candidate.album, 120))
        if candidate.year:
            album_line += f" · {candidate.year}"
        lines.append(f"💿 {album_line}")

    meta: list[str] = []
    if prepared.duration:
        meta.append(fmt_duration(prepared.duration))
    fmt = (prepared.file_format or "audio").upper()
    meta.append(fmt)
    bitrate = prepared.audio.probe.bitrate_kbps
    if bitrate:
        meta.append(f"{bitrate} kbps")
    category = prepared.audio.category
    if category is not QualityCategory.UNKNOWN:
        meta.append(category.label)
    if meta:
        lines.append("🎚 " + " · ".join(meta))

    source = candidate.provider_label or candidate.provider
    license_name = candidate.license_name
    source_line = f"🎧 {html.escape(_clip(source, 80))}"
    if license_name:
        source_line += f" · {html.escape(_clip(license_name, 80))}"
    lines.append(source_line)

    url = safe_url(candidate.source_url)
    if url:
        lines.append(f'🌐 <a href="{html.escape(url, quote=True)}">'
                     "Официальная страница</a>")

    caption = "\n".join(lines)
    if len(caption) > 1000:
        # не режем HTML-теги: убираем необязательные строки
        caption = "\n".join(lines[:3])[:1000]
    return caption


def _thumbnail(prepared: PreparedTrack) -> BufferedInputFile | None:
    cover = prepared.cover
    if cover is None or not cover.candidate.is_valid:
        return None
    thumb = make_thumbnail(cover.candidate.data)
    if not thumb:
        return None
    return BufferedInputFile(thumb, filename="cover.jpg")


def _title(prepared: PreparedTrack) -> str:
    return _clip(prepared.candidate.title or Path(prepared.path or "").stem, 64)


def _performer(prepared: PreparedTrack) -> str:
    return _clip(prepared.candidate.artist or "Неизвестный исполнитель", 64)


async def _source_file(prepared: PreparedTrack) -> str | FSInputFile:
    if prepared.telegram_file_id:
        return prepared.telegram_file_id
    if not prepared.path or not Path(prepared.path).exists():
        raise FileNotFoundError("Файл трека уже удалён из кэша")
    filename = telegram_filename(prepared.artist, prepared.title, prepared.file_format)
    return FSInputFile(prepared.path, filename=filename)


async def _remember_file_id(prepared: PreparedTrack, file_id: str,
                            file_unique_id: str = "") -> None:
    prepared.telegram_file_id = file_id
    prepared.telegram_file_unique_id = file_unique_id
    try:
        async with AsyncSessionLocal() as session:
            if prepared.cache_key:
                await set_telegram_file(session, prepared.cache_key, file_id,
                                        file_unique_id)
            if prepared.history_id:
                await repository.update_history_telegram(
                    session, prepared.history_id, file_id, file_unique_id)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("file_id cache: %s", exc)


def _cleanup(prepared: PreparedTrack) -> None:
    """Удаляет временный каталог задачи после успешной отправки."""
    try:
        path = Path(prepared.path).resolve()
        if path.is_relative_to(music_settings.tmp_dir.resolve()):
            cleanup_job_dir(path.parent.name)
    except (OSError, ValueError):
        pass


async def send_prepared(bot: types.Bot, chat_id: int, prepared: PreparedTrack, *,
                        send_mode: str = "auto",
                        reply_markup=None,
                        reply_to_message_id: int | None = None) -> types.Message:
    """Отправляет подготовленный трек. Возвращает сообщение Telegram."""
    kind = prepared.media_kind
    if send_mode == "document":
        kind = MediaKind.DOCUMENT
    elif send_mode == "audio":
        kind = MediaKind.AUDIO if prepared.file_format in ("mp3", "m4a") else MediaKind.DOCUMENT

    document = kind is MediaKind.DOCUMENT
    source = await _source_file(prepared)
    caption = build_caption(prepared)
    kwargs: dict = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
        "reply_markup": reply_markup,
        "disable_notification": False,
    }
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
        kwargs["allow_sending_without_reply"] = True

    if document:
        try:
            sent = await bot.send_document(
                document=source,
                filename=telegram_filename(prepared.artist, prepared.title,
                                           prepared.file_format),
                **kwargs)
        except TypeError:
            # filename не принимается для file_id — повторяем без него
            sent = await bot.send_document(document=source, **kwargs)
    else:
        audio_kwargs = dict(
            audio=source,
            title=_title(prepared),
            performer=_performer(prepared),
            duration=int(prepared.duration or 0),
            **kwargs)
        thumb = _thumbnail(prepared)
        if thumb is not None:
            audio_kwargs["thumbnail"] = thumb
        try:
            sent = await bot.send_audio(**audio_kwargs)
        except TelegramBadRequest as exc:
            if thumb is None:
                raise
            log.info("send_audio: thumbnail отклонён, повтор без него: %s", exc)
            audio_kwargs.pop("thumbnail", None)
            sent = await bot.send_audio(**audio_kwargs)

    media = sent.audio or sent.document
    file_id = getattr(media, "file_id", "") if media else ""
    unique_id = getattr(media, "file_unique_id", "") if media else ""
    if file_id:
        await _remember_file_id(prepared, file_id, unique_id)
    _cleanup(prepared)
    return sent


def _history_caption(history) -> str:
    title = history.title or "Без названия"
    artist = history.artist or "Неизвестный исполнитель"
    lines = [f"🎵 <b>{html.escape(artist)} — {html.escape(title)}</b>"]
    meta: list[str] = []
    if history.duration:
        meta.append(fmt_duration(history.duration))
    if history.audio_format:
        meta.append(str(history.audio_format).upper())
    if history.bitrate_kbps:
        meta.append(f"{history.bitrate_kbps} kbps")
    if history.quality_category:
        try:
            meta.append(QualityCategory(history.quality_category).label)
        except ValueError:
            pass
    if meta:
        lines.append("🎚 " + " · ".join(meta))
    if history.provider:
        lines.append(f"🎧 {html.escape(str(history.provider))}")
    url = safe_url(history.source_url)
    if url:
        lines.append(f'🌐 <a href="{html.escape(url, quote=True)}">Источник</a>')
    return "\n".join(lines)


async def send_history_entry(bot: types.Bot, chat_id: int, history, *,
                             cached_path: str = "", reply_markup=None) -> types.Message:
    """Повторная отправка из истории по file_id или файловому кэшу."""
    file_id = history.telegram_file_id or ""
    path = cached_path or ""
    if not file_id and not (path and Path(path).exists()):
        raise FileNotFoundError("Файл уже удалён из кэша")

    source: str | FSInputFile = file_id or FSInputFile(
        path, filename=telegram_filename(history.artist, history.title,
                                         history.audio_format or "audio"))
    caption = _history_caption(history)
    document = (history.media_kind or "audio") == "document"

    if document:
        try:
            sent = await bot.send_document(
                document=source, caption=caption, parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                filename=telegram_filename(history.artist, history.title,
                                           history.audio_format or "audio"))
        except TypeError:
            sent = await bot.send_document(
                document=source, caption=caption, parse_mode=ParseMode.HTML,
                reply_markup=reply_markup)
    else:
        sent = await bot.send_audio(
            audio=source, title=_clip(history.title or "", 64),
            performer=_clip(history.artist or "", 64),
            duration=int(history.duration or 0), caption=caption,
            parse_mode=ParseMode.HTML, reply_markup=reply_markup)

    media = sent.audio or sent.document
    new_id = getattr(media, "file_id", "") if media else ""
    unique_id = getattr(media, "file_unique_id", "") if media else ""
    if new_id and new_id != file_id:
        try:
            async with AsyncSessionLocal() as session:
                await repository.update_history_telegram(
                    session, history.id, new_id, unique_id)
                if history.cache_key:
                    await set_telegram_file(session, history.cache_key,
                                            new_id, unique_id)
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("history file_id: %s", exc)
    return sent


__all__ = ["build_caption", "safe_url", "send_prepared", "send_history_entry"]
