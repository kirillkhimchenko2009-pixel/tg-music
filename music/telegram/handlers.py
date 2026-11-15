"""Telegram-хаб музыки: /music, поиск, загрузка, файлы, избранное.

Роутер подключается ДО `search_router`: обычный текст перехватывается только
в FSM-состояниях музыки (и опционально при MUSIC_AUTO_TEXT=1), поэтому
legacy-поиск продолжает работать как раньше.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from .. import repository
from ..config import music_settings
from ..db import AsyncSessionLocal
from ..domain import DownloadOutcome, TrackCandidate, fmt_duration, human_size
from ..downloader.queue import queue
from ..enums import PermissionStatus, TrackVersion
from ..errors import DuplicateJobError, QueueFullError, UserLimitError
from ..models import MusicDownloadHistory, MusicTrack, MusicUserFavorite
from ..providers import get_provider, registry
from ..search.engine import engine as search_engine
from ..storage import (cleanup_job_dir, ensure_not_executable, guess_extension,
                       new_job_id, sanitize_filename, workspace_for)
from . import keyboards as kb
from . import sender
from .progress import ProgressView

log = logging.getLogger("music.telegram.handlers")
router = Router(name="music_hub")

_AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".webm"}
_SESSION_TTL = 30 * 60
_MAX_SESSIONS = 300
_SEARCH_TEXT_LIMIT = 3800


class MusicStates(StatesGroup):
    waiting_query = State()
    waiting_link = State()
    waiting_upload = State()


@dataclass
class SearchSession:
    sid: str
    user_id: int
    query: str
    results: list[TrackCandidate]
    created_at: float = field(default_factory=time.time)
    page: int = 0
    provider_errors: dict[str, str] = field(default_factory=dict)


_sessions: dict[str, SearchSession] = {}
_user_hits: dict[int, deque[float]] = {}


HUB_TEXT = (
    "🎵 <b>Музыка</b>\n\n"
    "Легальный поиск и загрузка из нескольких источников:\n"
    "• <b>Jamendo</b> — свободные лицензии Creative Commons\n"
    "• <b>Internet Archive</b> — только явные CC/PD\n"
    "• <b>SoundCloud</b> — только официальные загрузки автора\n"
    "• <b>Spotify</b> — метаданные и официальная ссылка\n"
    "• <b>Bandcamp</b> — официальная страница и покупка\n"
    "• <b>Твой файл</b> — загруженный тобой аудиофайл\n\n"
    "Я не обхожу DRM, подписки и гео-ограничения. Если файл скачать нельзя — "
    "покажу официальную ссылку."
)

HELP_TEXT = (
    "ℹ️ <b>Как это работает</b>\n\n"
    "1. Нажми «Найти музыку» и пришли название.\n"
    "2. Выбери подходящий вариант — бот проверит разрешение источника.\n"
    "3. Если автор разрешил скачивание, файл проверится анализатором качества, "
    "получит корректные метаданные и обложку.\n\n"
    "🔒 <b>Ограничения</b>\n"
    "• Скачиваем только при <code>DOWNLOAD_ALLOWED</code>.\n"
    "• Spotify/Bandcamp — официальные ссылки, без обхода покупки.\n"
    "• SoundCloud — только треки с кнопкой официального скачивания.\n"
    "• Internet Archive — только свободные лицензии.\n\n"
    "📥 MP3/M4A отправляются как аудио, FLAC/WAV/OGG/OPUS — файлом, "
    "без лишнего перекодирования."
)


# ---------------------------------------------------------------- утилиты

def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _allow(user_id: int, *, weight: int = 1) -> bool:
    limit = max(1, music_settings.rate_per_minute)
    now = time.monotonic()
    bucket = _user_hits.setdefault(int(user_id), deque())
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) + weight > limit:
        return False
    for _ in range(weight):
        bucket.append(now)
    return True


def _prune_sessions() -> None:
    cutoff = time.time() - _SESSION_TTL
    expired = [sid for sid, item in _sessions.items() if item.created_at < cutoff]
    for sid in expired:
        _sessions.pop(sid, None)
    if len(_sessions) > _MAX_SESSIONS:
        for sid, _item in sorted(_sessions.items(), key=lambda pair: pair[1].created_at):
            _sessions.pop(sid, None)
            if len(_sessions) <= _MAX_SESSIONS:
                break


def _store_session(user_id: int, query: str, results: list[TrackCandidate],
                   errors: dict[str, str] | None = None) -> SearchSession:
    _prune_sessions()
    sid = uuid.uuid4().hex[:10]
    session = SearchSession(sid=sid, user_id=int(user_id), query=query,
                            results=list(results), provider_errors=dict(errors or {}))
    _sessions[sid] = session
    return session


def _get_session(sid: str, user_id: int) -> SearchSession | None:
    session = _sessions.get(sid)
    if session is None:
        return None
    if session.user_id != int(user_id) or time.time() - session.created_at > _SESSION_TTL:
        return None
    return session


def _permission_badge(candidate: TrackCandidate) -> str:
    status = candidate.permission
    if status is PermissionStatus.DOWNLOAD_ALLOWED:
        return "✅ можно скачать"
    if status is PermissionStatus.STREAM_ONLY:
        return "🎧 только прослушивание"
    if status is PermissionStatus.OFFICIAL_LINK_ONLY:
        return "🌐 официальная ссылка"
    if status is PermissionStatus.PURCHASE_REQUIRED:
        return "💳 нужна покупка"
    if status is PermissionStatus.AUTH_REQUIRED:
        return "🔑 нужна авторизация"
    if status is PermissionStatus.BLOCKED:
        return "⛔ запрещено источником"
    return "❔ лицензия неясна"


def _provider_note(errors: dict[str, str]) -> str:
    if not errors:
        return ""
    labels: list[str] = []
    for name in errors:
        provider = registry.get(name)
        label = provider.display_name if provider else name
        if provider is not None and not provider.enabled:
            label += " (не настроен)"
        if label not in labels:
            labels.append(label)
    return "\n\n⚠️ Недоступно: " + ", ".join(labels[:4])


def _result_line(index: int, candidate: TrackCandidate) -> str:
    provider = candidate.provider_label or candidate.provider
    quality = candidate.quality_hint
    meta = " · ".join(x for x in (provider, candidate.duration_label,
                                  quality, _permission_badge(candidate)) if x)
    return (f"<b>{index}.</b> <b>{_esc(candidate.display)}</b>\n"
            f"    <i>{_esc(meta)}</i>")


def _results_text(session: SearchSession) -> str:
    lines = ["🎵 <b>Результаты поиска</b>",
             f"Запрос: <i>{_esc(session.query[:200])}</i>", ""]
    for index, candidate in enumerate(session.results, 1):
        lines.append(_result_line(index, candidate))
    text = "\n".join(lines)
    text += _provider_note(session.provider_errors)
    if len(text) > _SEARCH_TEXT_LIMIT:
        text = text[:_SEARCH_TEXT_LIMIT].rsplit("\n", 1)[0]
    return text


def _no_results_text(query: str, errors: dict[str, str]) -> str:
    text = (f"😔 По запросу <b>{_esc(query[:200])}</b> ничего не нашлось.\n\n"
            "Попробуй иначе: «Исполнитель — Название», без слов "
            "<i>official, lyrics, remix</i>; или пришли прямую ссылку.")
    return text + _provider_note(errors)


def _blocked_text(candidate: TrackCandidate) -> str:
    lines = [
        "⛔ <b>Скачивание недоступно</b>",
        f"<b>{_esc(candidate.display)}</b>",
        "",
        f"{_esc(candidate.permission.label)}: {_esc(candidate.permission_label)}",
    ]
    if candidate.license_name:
        lines.append(f"Лицензия: {_esc(candidate.license_name)}")
    if candidate.source_url:
        lines.append("Открой официальную страницу — там можно послушать или купить.")
    return "\n".join(lines)


async def _ensure_user(user: types.User) -> None:
    async with AsyncSessionLocal() as session:
        await repository.ensure_user(
            session, user.id, user.username, user.first_name or "")
        await session.commit()


async def _send_mode(user_id: int) -> str:
    try:
        async with AsyncSessionLocal() as session:
            settings = await repository.get_user_settings(session, user_id)
            mode = settings.send_mode or "auto"
            await session.commit()
            return mode if mode in ("auto", "audio", "document") else "auto"
    except Exception as exc:  # noqa: BLE001
        log.debug("send_mode: %s", exc)
        return "auto"


async def send_hub(message: types.Message) -> None:
    """Публичная точка входа для reply-кнопки «🎵 Музыка»."""
    await message.answer(HUB_TEXT, parse_mode="HTML",
                         reply_markup=kb.main_menu_kb(),
                         disable_web_page_preview=True)


# ---------------------------------------------------------------- команды

@router.message(Command("music"))
async def cmd_music(message: types.Message, state: FSMContext):
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        await _do_search(message, parts[1].strip())
        return
    await _ensure_user(message.from_user)
    await send_hub(message)


# ---------------------------------------------------------------- хаб

@router.callback_query(F.data == "mh:menu")
async def cb_menu(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text(HUB_TEXT, parse_mode="HTML",
                                   reply_markup=kb.main_menu_kb(),
                                   disable_web_page_preview=True)
    except TelegramBadRequest:
        await cb.message.answer(HUB_TEXT, parse_mode="HTML",
                                reply_markup=kb.main_menu_kb(),
                                disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data == "mh:search")
async def cb_search(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(MusicStates.waiting_query)
    try:
        await cb.message.edit_text(
            "🔎 <b>Что ищем?</b>\n\nПришли название, исполнителя или "
            "«Исполнитель — Название».\nМожно вставить ссылку на трек.",
            parse_mode="HTML", reply_markup=kb.search_prompt_kb(),
            disable_web_page_preview=True)
    except TelegramBadRequest:
        await cb.message.answer("🔎 Пришли название трека или ссылку.")
    await cb.answer()


@router.callback_query(F.data == "mh:link")
async def cb_link(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(MusicStates.waiting_link)
    text = (
        "🔗 <b>Ссылка на трек</b>\n\n"
        "Поддерживаются:\n"
        "• Jamendo\n• Internet Archive\n• Bandcamp\n• Spotify (метаданные)\n"
        "• SoundCloud (официальные загрузки)\n\n"
        "Пришли ссылку одним сообщением."
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=kb.search_prompt_kb(),
                                   disable_web_page_preview=True)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb.search_prompt_kb())
    await cb.answer()


@router.callback_query(F.data == "mh:upload")
async def cb_upload(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(MusicStates.waiting_upload)
    text = (
        "📂 <b>Свой файл</b>\n\n"
        "Пришли аудио как файл или аудиосообщение. Поддерживаются "
        "MP3, M4A/AAC, FLAC, WAV, OGG, OPUS.\n\n"
        f"Максимальный размер: {music_settings.max_file_mb} МБ.\n"
        "Файл останется твоим: я не публикую его никуда."
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=kb.search_prompt_kb())
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb.search_prompt_kb())
    await cb.answer()


@router.callback_query(F.data == "mh:help")
async def cb_help(cb: types.CallbackQuery):
    try:
        await cb.message.edit_text(HELP_TEXT, parse_mode="HTML",
                                   reply_markup=kb.back_menu_kb(),
                                   disable_web_page_preview=True)
    except TelegramBadRequest:
        await cb.message.answer(HELP_TEXT, reply_markup=kb.back_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "mh:noop")
async def cb_noop(cb: types.CallbackQuery):
    await cb.answer(
        "Глобальный авто-поиск задаётся переменной MUSIC_AUTO_TEXT в .env",
        show_alert=True)


# ---------------------------------------------------------------- поиск

async def _do_search(message: types.Message, query: str) -> None:
    query = " ".join((query or "").split())[:300]
    if not query:
        await message.answer("Пришли название трека или ссылку.")
        return
    if not _allow(message.from_user.id):
        await message.answer(
            "⏳ Слишком много запросов. Подожди минуту и попробуй снова.")
        return

    status = await message.answer("🔎 <b>Ищу по легальным источникам…</b>",
                                  parse_mode="HTML",
                                  disable_web_page_preview=True)
    try:
        response = await search_engine.search(query, limit=music_settings.search_results)
    except Exception as exc:  # noqa: BLE001
        log.exception("music search %r", query)
        await status.edit_text(
            "⚠️ Поиск не удался. Попробуй ещё раз или пришли другую ссылку.",
            reply_markup=kb.back_menu_kb())
        return

    if not response.results:
        await status.edit_text(_no_results_text(query, response.errors),
                               parse_mode="HTML",
                               reply_markup=kb.back_menu_kb(),
                               disable_web_page_preview=True)
        return

    session = _store_session(message.from_user.id, query, response.results,
                             response.errors)
    await status.edit_text(_results_text(session), parse_mode="HTML",
                           reply_markup=kb.results_kb(session.results, 0, session.sid),
                           disable_web_page_preview=True)


@router.message(StateFilter(MusicStates.waiting_query), F.text)
async def on_search_text(message: types.Message, state: FSMContext):
    query = (message.text or "").strip()
    if query.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await send_hub(message)
        return
    await state.clear()
    await _do_search(message, query)


@router.message(StateFilter(MusicStates.waiting_link), F.text)
async def on_link_text(message: types.Message, state: FSMContext):
    link = (message.text or "").strip()
    if link.lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await send_hub(message)
        return
    await state.clear()
    await _do_search(message, link)


# ---------------------------------------------------------------- пагинация

@router.callback_query(F.data.startswith("mh:p:"))
async def cb_page(cb: types.CallbackQuery):
    try:
        _tag, _page, sid, page = cb.data.split(":", 3)
        page = max(0, int(page))
    except (ValueError, AttributeError):
        await cb.answer("Кнопка устарела", show_alert=True)
        return
    session = _get_session(sid, cb.from_user.id)
    if session is None:
        await cb.answer("Результаты устарели — выполни поиск заново", show_alert=True)
        return
    session.page = page
    try:
        await cb.message.edit_text(
            _results_text(session), parse_mode="HTML",
            reply_markup=kb.results_kb(session.results, page, sid),
            disable_web_page_preview=True)
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("mh:b:"))
async def cb_back(cb: types.CallbackQuery):
    sid = cb.data.split(":", 2)[2]
    session = _get_session(sid, cb.from_user.id)
    if session is None:
        await cb.answer("Результаты устарели", show_alert=True)
        return
    try:
        await cb.message.edit_text(
            _results_text(session), parse_mode="HTML",
            reply_markup=kb.results_kb(session.results, session.page, sid),
            disable_web_page_preview=True)
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("mh:v:"))
async def cb_variants(cb: types.CallbackQuery):
    try:
        _tag, _variant, sid, index = cb.data.split(":", 3)
        index = int(index)
    except (ValueError, AttributeError):
        await cb.answer("Кнопка устарела", show_alert=True)
        return
    session = _get_session(sid, cb.from_user.id)
    if session is None or not (0 <= index < len(session.results)):
        await cb.answer("Результаты устарели", show_alert=True)
        return
    item = session.results[index]
    lines = [f"🔀 <b>Другие источники</b>",
             f"<b>{_esc(item.display)}</b>", ""]
    for alt_index, alt in enumerate(item.alternatives[:5], 1):
        lines.append(f"<b>{alt_index}.</b> {_esc(alt.provider_label or alt.provider)} · "
                     f"{_esc(alt.quality_hint)} · {_permission_badge(alt)}")
    if not item.alternatives:
        lines.append("Других источников для этой записи нет.")
    try:
        await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                                   reply_markup=kb.alternatives_kb(item, sid, index),
                                   disable_web_page_preview=True)
    except TelegramBadRequest:
        pass
    await cb.answer()


# ---------------------------------------------------------------- избранное

@router.callback_query(F.data.startswith("mh:f:"))
async def cb_favorite(cb: types.CallbackQuery):
    try:
        _tag, _fav, sid, index = cb.data.split(":", 3)
        index = int(index)
    except (ValueError, AttributeError):
        await cb.answer("Кнопка устарела", show_alert=True)
        return
    session = _get_session(sid, cb.from_user.id)
    if session is None or not (0 <= index < len(session.results)):
        await cb.answer("Результаты устарели", show_alert=True)
        return
    if not _allow(cb.from_user.id):
        await cb.answer("Слишком много действий, подожди минуту", show_alert=True)
        return
    item = session.results[index]
    try:
        async with AsyncSessionLocal() as db:
            await repository.ensure_user(
                db, cb.from_user.id, cb.from_user.username,
                cb.from_user.first_name or "")
            track = await repository.upsert_track(db, item)
            added = await repository.add_favorite(db, cb.from_user.id, track.id)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("music favorite")
        await cb.answer("Не удалось сохранить в избранное", show_alert=True)
        return
    await cb.answer("❤ Добавлено в избранное" if added else "Уже в избранном")


@router.callback_query(F.data == "mh:fav")
async def cb_favorites(cb: types.CallbackQuery):
    async with AsyncSessionLocal() as db:
        rows = await repository.list_favorites(db, cb.from_user.id, limit=10)
        await db.commit()
    if not rows:
        text = ("❤️ <b>Избранное</b>\n\nПока пусто. Найди трек и нажми ❤ "
                "в результатах поиска.")
        markup = kb.back_menu_kb()
    else:
        lines = ["❤️ <b>Избранное</b>",
                 "Нажми на трек, чтобы скачать его снова.", ""]
        for _favorite, track in rows:
            artist = track.artist_name or "Неизвестный исполнитель"
            album = f" · {_esc(track.album_name)}" if track.album_name else ""
            lines.append(f"• <b>{_esc(artist)} — {_esc(track.title)}</b>{album}")
        text = "\n".join(lines)
        markup = kb.favorites_kb(rows)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=markup,
                                   disable_web_page_preview=True)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("mh:fs:"))
async def cb_favorite_download(cb: types.CallbackQuery):
    try:
        track_id = int(cb.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await cb.answer("Кнопка устарела", show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        favorite = (await db.execute(
            select(MusicUserFavorite).where(
                MusicUserFavorite.user_id == cb.from_user.id,
                MusicUserFavorite.track_id == track_id))).scalar_one_or_none()
        track = await db.get(MusicTrack, track_id) if favorite else None
        await db.commit()
    if track is None:
        await cb.answer("Трек не найден в избранном", show_alert=True)
        return
    candidate = _candidate_from_track(track)
    await _start_download(cb, candidate, precheck=False)


@router.callback_query(F.data.startswith("mh:ad:"))
async def cb_alternative_download(cb: types.CallbackQuery):
    try:
        _tag, _alt, sid, index, alt_index = cb.data.split(":", 4)
        index, alt_index = int(index), int(alt_index)
    except (ValueError, AttributeError):
        await cb.answer("Кнопка устарела", show_alert=True)
        return
    session = _get_session(sid, cb.from_user.id)
    if session is None or not (0 <= index < len(session.results)):
        await cb.answer("Результаты устарели", show_alert=True)
        return
    alternatives = session.results[index].alternatives
    if not (0 <= alt_index < len(alternatives)):
        await cb.answer("Вариант исчез, обнови поиск", show_alert=True)
        return
    await _start_download(cb, alternatives[alt_index], precheck=False)


# ---------------------------------------------------------------- загрузка

def _candidate_from_track(track: MusicTrack) -> TrackCandidate:
    try:
        version = TrackVersion(track.version or "original")
    except ValueError:
        version = TrackVersion.ORIGINAL
    return TrackCandidate(
        provider=track.provider,
        external_id=track.external_id,
        title=track.title,
        artist=track.artist_name or "",
        album=track.album_name or "",
        duration=int(track.duration or 0),
        year=None,
        cover_url=track.cover_url,
        source_url=track.source_url or "",
        permission=PermissionStatus.LICENSE_UNKNOWN,
        license_name=track.license_name or "",
        license_url=track.license_url or "",
        isrc=track.isrc,
        version=version,
        explicit=bool(track.explicit),
        file_format=None,
        track_id=track.id,
        extra=dict(track.extra or {}),
    )


@router.callback_query(F.data.startswith("mh:d:"))
async def cb_download(cb: types.CallbackQuery):
    try:
        _tag, _dl, sid, index = cb.data.split(":", 3)
        index = int(index)
    except (ValueError, AttributeError):
        await cb.answer("Кнопка устарела", show_alert=True)
        return
    session = _get_session(sid, cb.from_user.id)
    if session is None or not (0 <= index < len(session.results)):
        await cb.answer("Результаты устарели — выполни поиск заново",
                        show_alert=True)
        return
    await _start_download(cb, session.results[index])


# --------------- распределённая роль app: качают воркеры (Релиз 2/3) ---------------

_REMOTE_WATCHERS: set[asyncio.Task] = set()


def _active_limit() -> int:
    return max(1, int(os.getenv("WEBAPP_MAX_ACTIVE_DOWNLOADS", "3")))


async def _start_remote_download(cb: types.CallbackQuery,
                                 candidate: TrackCandidate,
                                 view: ProgressView, fallback_url: str) -> None:
    """MEDIA_ROLE=app: скачивание выполняют воркеры через очередь в Neon.

    Воркер сам пришлёт файл в чат (публичного HTTP у воркеров нет), бот
    здесь только ставит джобу и рисует прогресс по её статусу. Кнопки
    «отменить» у удалённых джоб пока нет — неактуальные задачи дворятся
    TTL lease'а."""
    from core.config import settings
    from services import catalog
    from .. import mediajobs

    user_id = cb.from_user.id
    try:
        async with AsyncSessionLocal() as s:
            t = await catalog.upsert_track(s, mediajobs.candidate_meta(candidate))
            await s.commit()
            if await mediajobs.find_active(s, t.id, user_id) is not None:
                await view.finish("⏳ Эта загрузка уже выполняется…",
                                  reply_markup=kb.back_menu_kb())
                return
            if await mediajobs.count_active(s, user_id) >= _active_limit():
                await view.finish(
                    "⏳ Слишком много одновременных загрузок. "
                    "Дождись завершения текущих.",
                    reply_markup=kb.back_menu_kb())
                return
            job = await mediajobs.create_job(
                s, track_id=t.id, uid=user_id,
                quality=settings.DEFAULT_QUALITY, source=t.source)
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("remote enqueue")
        await view.finish(
            f"⚠️ Не удалось поставить задачу: {_esc(type(exc).__name__)}",
            reply_markup=kb.back_menu_kb())
        return

    await view.update("queued", 0, force=True)
    task = asyncio.create_task(_watch_remote_job(view, job.id, fallback_url),
                               name=f"media-watch-{job.id}")
    _REMOTE_WATCHERS.add(task)
    task.add_done_callback(_REMOTE_WATCHERS.discard)


async def _watch_remote_job(view: ProgressView, job_id: str, fallback_url: str,
                            delay: float = 3.0, max_sec: float = 45 * 60.0) -> None:
    """Статус джобы в Neon → прогресс в сообщении бота до терминала."""
    from .. import mediajobs

    deadline = time.monotonic() + max_sec
    last_pct = -1
    while time.monotonic() < deadline:
        await asyncio.sleep(delay)
        try:
            async with AsyncSessionLocal() as s:
                row = await mediajobs.get_job(s, job_id)
        except Exception:  # noqa: BLE001
            continue                    # сеть/БД моргнули — вотчер не падает
        if row is None:
            return
        if row.status == "done":
            note = " (30-сек фрагмент)" if row.preview else ""
            await view.finish(f"✅ <b>Готово — файл отправлен в чат</b>{note}",
                              reply_markup=kb.back_menu_kb())
            return
        if row.status == "error":
            lines = ["⚠️ <b>Не удалось получить файл</b>"]
            if row.error:
                lines.append(_esc(row.error[:500]))
            await view.finish("\n".join(lines),
                              reply_markup=kb.error_kb(fallback_url))
            return
        pct = int(row.progress or 0)
        if pct != last_pct:
            last_pct = pct
            step = "download" if row.status == "leased" else "queued"
            await view.update(step, pct)


async def _start_download(cb: types.CallbackQuery, candidate: TrackCandidate,
                          *, precheck: bool = True) -> None:
    if not _allow(cb.from_user.id):
        await cb.answer("Слишком много действий, подожди минуту", show_alert=True)
        return
    if precheck and candidate.permission not in (
            PermissionStatus.DOWNLOAD_ALLOWED, PermissionStatus.LICENSE_UNKNOWN):
        await cb.answer(f"⛔ {candidate.permission.label}", show_alert=True)
        await cb.message.answer(_blocked_text(candidate), parse_mode="HTML",
                                reply_markup=kb.error_kb(candidate.source_url),
                                disable_web_page_preview=True)
        return

    if queue.find_active(cb.from_user.id, candidate) is not None:
        await cb.answer("⏳ Эта загрузка уже выполняется", show_alert=True)
        return

    await cb.answer("Ставлю в очередь…")
    try:
        await _ensure_user(cb.from_user)
    except Exception:  # noqa: BLE001
        pass
    progress_msg = await cb.message.answer(
        f"⏳ <b>В очереди:</b> {_esc(candidate.display)}", parse_mode="HTML")
    view = ProgressView(progress_msg, title=candidate.display)
    user_id = cb.from_user.id
    chat_id = progress_msg.chat.id
    fallback_url = candidate.source_url or ""

    if music_settings.media_role == "app":
        # Релиз 1.5: кнопки хаба тоже качают через воркеров — иначе при
        # MEDIA_ROLE=app локальная очередь всё равно гоняла бы yt-dlp
        # на Render (источник OOM). Локальный путь остаётся для ролей
        # local/worker и upload-флоу (файл юзера уже на диске APP-хоста).
        await _start_remote_download(cb, candidate, view, fallback_url)
        return

    async def on_done(outcome: DownloadOutcome) -> None:
        await _deliver(cb.bot, chat_id, user_id, view, outcome, fallback_url)

    try:
        job = await queue.enqueue(
            user_id, candidate, on_progress=view.update, on_done=on_done)
    except UserLimitError as exc:
        await view.finish(f"⏳ {_esc(str(exc))}", reply_markup=kb.back_menu_kb())
        return
    except DuplicateJobError:
        await view.finish("⏳ Эта загрузка уже выполняется…",
                          reply_markup=kb.back_menu_kb())
        return
    except QueueFullError:
        await view.finish("⏳ Очередь переполнена. Попробуй чуть позже.",
                          reply_markup=kb.back_menu_kb())
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("music enqueue")
        await view.finish(
            f"⚠️ Не удалось поставить задачу: {_esc(type(exc).__name__)}",
            reply_markup=kb.back_menu_kb())
        return
    try:
        await progress_msg.edit_reply_markup(reply_markup=kb.cancel_kb(job.id))
    except Exception:  # noqa: BLE001
        pass


async def _deliver(bot: types.Bot, chat_id: int, user_id: int, view: ProgressView,
                   outcome: DownloadOutcome, fallback_url: str = "") -> None:
    if not outcome.ok:
        if outcome.error_code == "CANCELLED":
            await view.finish("✖ Загрузка отменена", reply_markup=kb.back_menu_kb())
            return
        lines = ["⚠️ <b>Не удалось получить файл</b>"]
        if outcome.error:
            lines.append(_esc(outcome.error[:500]))
        if outcome.permission and not outcome.permission.can_download:
            lines.append("")
            lines.append(f"{_esc(outcome.permission.label)}. "
                         f"{_esc(outcome.permission.reason)}")
        official = outcome.official_url or fallback_url
        await view.finish("\n".join(lines), reply_markup=kb.error_kb(official))
        return

    prepared = outcome.prepared
    if prepared is None:
        await view.finish("⚠️ Файл не подготовлен", reply_markup=kb.back_menu_kb())
        return
    try:
        send_mode = await _send_mode(user_id)
        await sender.send_prepared(bot, chat_id, prepared, send_mode=send_mode)
    except FileNotFoundError:
        await view.finish(
            "⚠️ Файл уже удалён из кэша. Запусти загрузку ещё раз.",
            reply_markup=kb.error_kb(outcome.official_url or fallback_url))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("music send")
        await view.finish(
            f"⚠️ Telegram не принял файл: {_esc(type(exc).__name__)}. "
            "Попробуй ещё раз или открой официальную ссылку.",
            reply_markup=kb.error_kb(outcome.official_url or fallback_url))
        return

    note = " · из кэша" if prepared.cache_hit else ""
    text = (f"✅ <b>Готово{note}</b>\n"
            f"🎵 <b>{_esc(prepared.artist or 'Неизвестный исполнитель')} — "
            f"{_esc(prepared.title)}</b>\n"
            f"🎚 {_esc(prepared.file_format.upper())} · "
            f"{_esc(prepared.audio.category.label)} · "
            f"{_esc(human_size(prepared.size))}")
    await view.finish(text, reply_markup=kb.back_menu_kb())


@router.callback_query(F.data.startswith("mh:c:"))
async def cb_cancel(cb: types.CallbackQuery):
    job_id = cb.data.split(":", 2)[2]
    cancelled = await queue.cancel(job_id, cb.from_user.id)
    await cb.answer("Отменяю…" if cancelled else "Задача уже завершена")


# ---------------------------------------------------------------- загрузка файла

@router.message(StateFilter(MusicStates.waiting_upload), Command("cancel"))
async def upload_cancel_command(message: types.Message, state: FSMContext):
    await state.clear()
    await send_hub(message)


@router.message(StateFilter(MusicStates.waiting_upload), F.text)
async def upload_text(message: types.Message, state: FSMContext):
    if (message.text or "").strip().lower() in ("отмена", "cancel", "/cancel"):
        await state.clear()
        await send_hub(message)
        return
    await message.answer(
        "Пришли именно аудиофайл (MP3/M4A/FLAC/WAV/OGG/OPUS) или нажми "
        "«Отмена» в меню.")


@router.message(StateFilter(MusicStates.waiting_upload), F.audio | F.document)
async def upload_file(message: types.Message, state: FSMContext):
    media = message.audio or message.document
    if media is None:
        return
    if not _allow(message.from_user.id):
        await message.answer("⏳ Слишком много загрузок. Подожди минуту.")
        return

    original_name = getattr(media, "file_name", "") or ""
    mime = getattr(media, "mime_type", "") or ""
    size = int(getattr(media, "file_size", 0) or 0)
    ext = Path(original_name).suffix.lower()
    if ext not in _AUDIO_EXT:
        ext = guess_extension(mime, original_name, ".bin")
    if ext not in _AUDIO_EXT:
        await message.answer(
            "⚠️ Не понял формат. Пришли MP3, M4A/AAC, FLAC, WAV, OGG или OPUS.")
        return
    if size and size > music_settings.max_file_mb * 1024 * 1024:
        await message.answer(
            f"⚠️ Файл больше {music_settings.max_file_mb} МБ. "
            "Пришли файл меньшего размера.")
        return

    seed = new_job_id()
    workspace = workspace_for(seed)
    safe_name = sanitize_filename(original_name or f"upload{ext}", fallback="upload",
                                  max_len=100)
    if not Path(safe_name).suffix:
        safe_name += ext
    dest = workspace / ensure_not_executable(safe_name)
    try:
        await message.bot.download(media, destination=dest)
    except Exception as exc:  # noqa: BLE001
        log.info("upload download: %s", exc)
        cleanup_job_dir(seed)
        await message.answer("⚠️ Не удалось скачать файл из Telegram. Попробуй ещё раз.")
        return

    await state.clear()
    status = await message.answer("📂 <b>Файл получен. Проверяю…</b>",
                                  parse_mode="HTML")
    try:
        from ..providers.user_upload import UserUploadProvider
        provider = UserUploadProvider()
        candidate = await asyncio.to_thread(
            provider.describe, str(dest), original_name=original_name or dest.name,
            title=getattr(media, "title", "") or "",
            artist=getattr(media, "performer", "") or "",
            duration=int(getattr(media, "duration", 0) or 0))
    except Exception as exc:  # noqa: BLE001
        log.exception("upload describe")
        cleanup_job_dir(seed)
        await status.edit_text("⚠️ Файл не похож на поддерживаемое аудио.")
        return

    candidate.extra["force_upload"] = True
    user_id = message.from_user.id
    chat_id = message.chat.id
    if queue.find_active(user_id, candidate) is not None:
        cleanup_job_dir(seed)
        await status.edit_text("⏳ Этот файл уже обрабатывается…")
        return
    view = ProgressView(status, title=candidate.display)

    async def on_done(outcome: DownloadOutcome) -> None:
        try:
            await _deliver(message.bot, chat_id, user_id, view, outcome, "")
        finally:
            # seed-каталог больше не нужен: менеджер уже скопировал файл
            cleanup_job_dir(seed)

    try:
        job = await queue.enqueue(
            user_id, candidate, on_progress=view.update, on_done=on_done)
    except UserLimitError as exc:
        cleanup_job_dir(seed)
        await view.finish(f"⏳ {_esc(str(exc))}", reply_markup=kb.back_menu_kb())
        return
    except DuplicateJobError:
        cleanup_job_dir(seed)
        await view.finish("⏳ Этот файл уже обрабатывается…",
                          reply_markup=kb.back_menu_kb())
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("upload enqueue")
        cleanup_job_dir(seed)
        await view.finish(f"⚠️ Не удалось поставить задачу: {_esc(type(exc).__name__)}",
                          reply_markup=kb.back_menu_kb())
        return
    try:
        await status.edit_reply_markup(reply_markup=kb.cancel_kb(job.id))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- история

@router.callback_query(F.data == "mh:hist")
async def cb_history(cb: types.CallbackQuery):
    async with AsyncSessionLocal() as db:
        rows = await repository.list_history(db, cb.from_user.id, limit=10)
        await db.commit()
    if not rows:
        text = ("📚 <b>История</b>\n\nПока пусто. Скачай первый трек через "
                "поиск или по ссылке.")
        markup = kb.back_menu_kb()
    else:
        lines = ["📚 <b>История загрузок</b>", ""]
        for index, row in enumerate(rows, 1):
            artist = row.artist or "Неизвестный исполнитель"
            when = row.downloaded_at.strftime("%d.%m.%Y") if row.downloaded_at else ""
            fmt = (row.audio_format or "").upper()
            meta = " · ".join(x for x in (when, fmt,
                                           f"{row.bitrate_kbps} kbps"
                                           if row.bitrate_kbps else "") if x)
            lines.append(f"<b>{index}.</b> {_esc(artist)} — {_esc(row.title)}")
            if meta:
                lines.append(f"    <i>{_esc(meta)}</i>")
        text = "\n".join(lines)
        markup = kb.history_kb(rows)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=markup,
                                   disable_web_page_preview=True)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("mh:rh:"))
async def cb_history_replay(cb: types.CallbackQuery):
    try:
        history_id = int(cb.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await cb.answer("Кнопка устарела", show_alert=True)
        return
    cached_path = ""
    async with AsyncSessionLocal() as db:
        row = await db.get(MusicDownloadHistory, history_id)
        if row is None or row.user_id != cb.from_user.id:
            await cb.answer("Запись не найдена", show_alert=True)
            return
        if not row.telegram_file_id and row.cache_key:
            cached = await repository.get_cached(db, row.cache_key)
            if cached is not None:
                cached_path = cached.path
        await db.commit()
    try:
        await sender.send_history_entry(cb.bot, cb.message.chat.id, row,
                                        cached_path=cached_path)
        await cb.answer("Отправляю…")
    except FileNotFoundError:
        await cb.answer("Файл уже удалён из кэша — найди трек заново",
                        show_alert=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("history replay")
        await cb.answer("Не удалось отправить файл", show_alert=True)


# ---------------------------------------------------------------- настройки

@router.callback_query(F.data == "mh:set")
async def cb_settings(cb: types.CallbackQuery):
    async with AsyncSessionLocal() as db:
        settings = await repository.get_user_settings(db, cb.from_user.id)
        send_mode = settings.send_mode or "auto"
        await db.commit()
    text = (
        "⚙️ <b>Настройки отправки</b>\n\n"
        "• <b>Авто</b> — MP3/M4A как аудио, остальное файлом.\n"
        "• <b>Аудио</b> — MP3/M4A как аудио, lossless остаётся файлом.\n"
        "• <b>Документ</b> — всё отправлять файлом, без сжатия.\n\n"
        "Файлы никогда не перекодируются без явного запроса."
    )
    try:
        await cb.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=kb.settings_kb(send_mode, music_settings.auto_text),
            disable_web_page_preview=True)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb.settings_kb(
            send_mode, music_settings.auto_text))
    await cb.answer()


@router.callback_query(F.data.startswith("mh:set:"))
async def cb_set_mode(cb: types.CallbackQuery):
    mode = cb.data.split(":", 2)[2]
    if mode not in ("auto", "audio", "document"):
        await cb.answer("Неизвестный режим", show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        await repository.ensure_user(
            db, cb.from_user.id, cb.from_user.username, cb.from_user.first_name or "")
        await repository.update_user_settings(db, cb.from_user.id, send_mode=mode)
        await db.commit()
    try:
        await cb.message.edit_reply_markup(
            reply_markup=kb.settings_kb(mode, music_settings.auto_text))
    except TelegramBadRequest:
        pass
    await cb.answer("Сохранено")


# ---------------------------------------------------------------- авто-поиск

if music_settings.auto_text:
    @router.message(F.text, ~F.text.startswith("/"))
    async def auto_search(message: types.Message, state: FSMContext):
        if await state.get_state() is not None:
            raise SkipHandler
        await _do_search(message, message.text or "")


__all__ = ["router", "MusicStates", "send_hub", "HUB_TEXT"]
