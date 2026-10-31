"""Фолбэк-источник: сторонние ТГ-боты-музыканты.

Список задаётся env `TG_BOTS` (usernames через запятую/пробел), например:
    TG_BOTS=@DeezerMusicBot @spotify_down_bot @my_music_bot

Как работает: наш бот отправляет стороннему боту сообщение «артист — название»
в приватный чат с ним; когда тот пришлёт аудио (audio/document-mp3) в этот же
чат — ловим апдейт и копируем file_id в чат пользователя. Копирование не тратит
нашу полосу: файл берётся прямо из хранилища Telegram, а сайт-источник качает
третий бот со СВОЕГО IP — обходит любые блокировки каталогов/гео.
"""
import asyncio
import logging
import time

from aiogram import F, Router, types

from core.config import settings

router = Router(name="musicbot")
log = logging.getLogger("bot.musicbot")

# ключ: (username источника, chat_id) → Future с file_id
_pending: dict[tuple[str, int], asyncio.Future] = {}
_last_sent: dict[str, float] = {}

_AUDIO_FILTER = (F.audio.exists()
                 | (F.document.exists()
                    & F.document.mime_type.startswith("audio")))


def bot_names() -> list[str]:
    """Список источников с очисткой '@' и приведением к нижнему регистру."""
    out, seen = [], set()
    for u in settings.TG_BOTS or []:
        name = u.strip().lstrip("@").lower()
        if name and name not in seen and name != "self":
            seen.add(name)
            out.append(name)
    return out


@router.message(F.chat.type == "private", _AUDIO_FILTER, F.from_user.exists())
async def on_source_audio(msg: types.Message):
    """Аудио от стороннего бота → отдаём ждущему Future (если запрос есть)."""
    uname = ((msg.from_user.username or "") if msg.from_user else "").lower()
    if uname not in set(bot_names()):
        return
    fut = _pending.pop((uname, msg.chat.id), None)
    if not fut or fut.done():
        return
    if msg.audio:
        fut.set_result(msg.audio.file_id)
    elif msg.document:
        fut.set_result(msg.document.file_id)


async def request_track(bot, user_id: int, artist: str, title: str) -> bool:
    """Опрашивает ботов по очереди; первый пришедший файл уходит в чат user_id.

    Возвращает True, если файл доставлен."""
    names = bot_names()
    if not names:
        return False
    query = " — ".join(x for x in (artist or "", title or "") if x).strip(" —")
    if not query:
        return False

    for uname in names:
        # анти-спам: между запросами к одному боту — пауза
        delta = time.monotonic() - _last_sent.get(uname, 0.0)
        if settings.TG_BOTS_COOLDOWN > delta:
            await asyncio.sleep(settings.TG_BOTS_COOLDOWN - delta)
        try:
            await bot.send_message(uname, query)
        except Exception as e:  # нет такого юзера / flood /私信 бот блокирует
            log.info("tgbot %s: запрос не отправлен: %s: %s",
                     uname, type(e).__name__, str(e)[:80])
            continue
        _last_sent[uname] = time.monotonic()

        key = (uname, user_id)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        _pending[key] = fut
        try:
            fid = await asyncio.wait_for(fut, timeout=settings.TG_BOTS_WAIT)
        except asyncio.TimeoutError:
            fid = None
        finally:
            f = _pending.get(key)
            if f and not f.done():
                f.set_result(None)
            _pending.pop(key, None)
        if not fid:
            log.info("tgbot %s: нет аудио-ответа (%s) за %.0fс",
                     uname, query[:48], settings.TG_BOTS_WAIT)
            continue
        try:
            await bot.send_audio(user_id, fid,
                                 caption=f"🎧 {query} — от @{uname}")
            log.info("tgbot %s: отдал аудио «%s» в чат %s",
                     uname, query[:48], user_id)
            return True
        except Exception as e:
            log.warning("tgbot %s: не смогли отправить файл: %s: %s",
                        uname, type(e).__name__, str(e)[:80])
    return False
