"""Рантайм-помощники: пользователи, отправка треков с кешем file_id и троттлингом."""
import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.models import (Playlist, PlaylistTrack, Track, User, utcnow)
from services import catalog
from services.providers.base import TrackMeta

log = logging.getLogger("bot.runtime")

def get_session() -> AsyncSession:
    from core.db import AsyncSessionLocal
    return AsyncSessionLocal()

FAVORITES_TITLE = "❤ Любимое"

async def get_favorites_playlist(session: AsyncSession, user_id: int) -> Playlist | None:
    """Системный плейлист «Любимое» пользователя (создаётся при первом лайке)."""
    user = await session.get(User, user_id)
    if not user:
        await ensure_user(session, user_id, None, "")
    pl = (await session.execute(select(Playlist).where(
        Playlist.owner_id == user_id, Playlist.title == FAVORITES_TITLE))).scalar_one_or_none()
    if not pl:
        pl = Playlist(owner_id=user_id, title=FAVORITES_TITLE)
        session.add(pl)
        await session.flush()
    return pl

async def favorited_ids(session: AsyncSession, user_id: int,
                        track_ids: list[int]) -> set[int]:
    """Какие из перечисленных треков уже в «Любимом»."""
    if not track_ids:
        return set()
    pl = (await session.execute(select(Playlist.id).where(
        Playlist.owner_id == user_id, Playlist.title == FAVORITES_TITLE))).scalar_one_or_none()
    if not pl:
        return set()
    rows = await session.execute(select(PlaylistTrack.track_id).where(
        PlaylistTrack.playlist_id == pl, PlaylistTrack.track_id.in_(track_ids)))
    return set(rows.scalars())

async def toggle_favorite(session: AsyncSession, user_id: int, track_id: int) -> bool:
    """Лайк/дизлайк: True = теперь в «Любимом», False = убран."""
    pl = await get_favorites_playlist(session, user_id)
    if pl is None:
        return False
    exists = (await session.execute(select(PlaylistTrack.id).where(
        PlaylistTrack.playlist_id == pl.id, PlaylistTrack.track_id == track_id))).first()
    if exists:
        await session.execute(
            delete(PlaylistTrack).where(PlaylistTrack.id == exists[0]))
        await session.commit()
        return False
    await playlist_add_track(session, pl.id, track_id)
    return True

async def ensure_user(session: AsyncSession, tg_id: int, username: str | None,
                      first_name: str) -> User:
    user = (await session.execute(select(User).where(User.id == tg_id))).scalar_one_or_none()
    if not user:
        user = User(id=tg_id, username=username, first_name=first_name,
                    is_admin=tg_id in settings.ADMIN_IDS)
        session.add(user)
        await session.commit()
    return user

async def save_file_id(session: AsyncSession, track_id: int, file_id: str,
                       quality: str | None = None, size: int | None = None,
                       local_path: str | None = None):
    track = await catalog.get_track(session, track_id)
    if track:
        track.file_id = file_id
        track.quality = quality or track.quality
        track.downloads += 1
        if size:
            track.size_bytes = size
        if local_path:
            track.local_path = local_path
        await session.commit()


async def save_file_id_retry(track_id: int, file_id: str, quality: str | None = None,
                             size: int | None = None, local_path: str | None = None,
                             attempts: int = 4) -> bool:
    """Кэширование file_id в НОВОЙ сессии с ретраями: при шторме
    «database is locked» (долгие транзакции на фоне сетевых доставок)
    кэш всё равно дожимается, когда база освободится."""
    last = None
    for i in range(attempts):
        try:
            async with get_session() as s2:
                await save_file_id(s2, track_id, file_id, quality, size, local_path)
            return True
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(2 * (i + 1))
    log.warning("save_file_id не удался за %s попыток (track %s): %s",
                attempts, track_id, last)
    return False

async def _fav_state(chat_id: int, track_id: int) -> bool:
    """Лайкнут ли трек этим юзером (в личке chat_id == user_id)."""
    if chat_id <= 0:
        return False
    try:
        async with get_session() as s:
            return bool(await favorited_ids(s, chat_id, [track_id]))
    except Exception:
        return False

_STALE_FILE_MARKERS = (
    "wrong file identifier",
    "file is not found",
    "file reference",
    "invalid file_id",
    "file_id is invalid",
)


def _stale_file_error(exc: Exception) -> bool:
    """Telegram отверг именно file_id (а не чат/сеть) — кеш можно чистить."""
    text = (getattr(exc, "message", "") or str(exc)).lower()
    return any(marker in text for marker in _STALE_FILE_MARKERS)


async def send_track_cached(bot: Bot, chat_id: int, track: Track) -> bool:
    """Быстрая отправка трека из кеша file_id. False, если кеша нет."""
    if not track.file_id:
        return False
    from bot.keyboards import action_kb
    from aiogram.exceptions import (TelegramBadRequest, TelegramNetworkError,
                                    TelegramRetryAfter)
    try:
        in_fav = await _fav_state(chat_id, track.id)
        for attempt in range(3):
            try:
                msg = await bot.send_audio(chat_id, track.file_id,
                                           title=track.title or "Трек",
                                           performer=track.artist or None,
                                           reply_markup=action_kb(track.id, in_fav))
                from bot import fx
                await fx.react(bot, chat_id, msg.message_id, fx.OK)
                return True
            except TelegramRetryAfter as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(min(e.retry_after + 1, 60))
            except TelegramNetworkError:
                if attempt == 2:
                    raise
                await asyncio.sleep(3 * (attempt + 1))
        return False
    except TelegramBadRequest as e:
        # Чистим кеш только если виноват сам file_id. «chat not found»,
        # «bot was blocked» и прочие ошибки чата не делают файл негодным:
        # иначе один неудачный чат обнулял бы общий кеш трека.
        if _stale_file_error(e):
            await save_file_id_retry(track.id, "")
        else:
            log.warning("send_track_cached: Telegram отклонил трек %s: %s",
                        track.id, e)
        return False
    except Exception as e:  # noqa: BLE001
        log.warning("send_track_cached: трек %s не отправлен: %s", track.id, e)
        return False

async def send_audio_file(bot: Bot, chat_id: int, track: Track, path) -> str | None:
    """Загрузка уже скачанного файла в ТГ; возвращает file_id.

    Без обложки (thumbnail): Telegram рисует из неё размытый фон плеера
    (просили «без блюра»), а заодно отпадают скачивание обложки и её
    аплоад на каждый трек — в пачках это минуты. Квадрат в списке чата
    станет иконкой ноты — плата за чистый плеер."""
    from bot.keyboards import action_kb
    from bot import fx
    in_fav = await _fav_state(chat_id, track.id)
    msg = None
    from aiogram.exceptions import (TelegramBadRequest, TelegramEntityTooLarge,
                                    TelegramNetworkError, TelegramRetryAfter)
    fx.typing(chat_id, bot, ChatAction.UPLOAD_DOCUMENT)
    for attempt in range(3):
        try:
            # chunk_size 1 МиБ: дефолтные 64 КиБ душат скорость аплоада —
            # именно фаза upload чаще всего «зависает»
            msg = await bot.send_audio(
                chat_id, FSInputFile(path, chunk_size=1 << 20),
                title=track.title or "Трек", performer=track.artist or None,
                request_timeout=300,
                reply_markup=action_kb(track.id, in_fav),
            )
            break
        except TelegramRetryAfter as e:
            if attempt == 2:
                raise
            await asyncio.sleep(min(e.retry_after + 1, 60))
        except TelegramNetworkError:
            if attempt == 2:
                raise
            await asyncio.sleep(3 * (attempt + 1))
    if msg is None:
        raise TelegramNetworkError(method=None, message="send failed after retries")
    from bot import fx
    await fx.react(bot, chat_id, msg.message_id, fx.OK)
    audio = msg.audio
    if audio:
        return audio.file_id
    # Telegram мог переклассифицировать файл в документ — это всё равно
    # полный трек, и кешировать его file_id полезно.
    doc = getattr(msg, "document", None)
    return doc.file_id if doc else None

async def get_or_create_playlist(session: AsyncSession, user_id: int, title: str) -> Playlist:
    """ tolerant-выборка: у юзера могло скопиться 2+ плейлиста с одним именем
        (гонки/лайк-плейлист до миграции) — берём старейший, не роняя /api. """
    rows = (await session.execute(
        select(Playlist).where(Playlist.owner_id == user_id, Playlist.title == title)
        .order_by(Playlist.id.asc()))).scalars().all()
    if rows:
        return rows[0]
    pl = Playlist(owner_id=user_id, title=title)
    session.add(pl)
    await session.flush()
    return pl

async def playlist_add_track(session: AsyncSession, playlist_id: int, track_id: int) -> bool:
    exists = (await session.execute(
        select(PlaylistTrack.id).where(PlaylistTrack.playlist_id == playlist_id,
                                       PlaylistTrack.track_id == track_id))).first()
    if exists:
        return False
    count = await playlist_count(session, playlist_id)
    session.add(PlaylistTrack(playlist_id=playlist_id, track_id=track_id, position=count))
    await session.commit()
    return True

async def playlist_count(session: AsyncSession, playlist_id: int) -> int:
    from sqlalchemy import func
    q = await session.execute(
        select(func.count()).select_from(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist_id))
    return q.scalar() or 0

class ChatThrottle:
    """Троттлер: не чаще interval секунд на чат (учитывая 429)."""
    def __init__(self, interval: float = 1.05):
        self.interval = interval
        self._last: dict[int, float] = {}

    async def wait(self, chat_id: int):
        now = time.monotonic()
        last = self._last.get(chat_id, 0)
        delta = now - last
        if delta < self.interval:
            await asyncio.sleep(self.interval - delta)
        self._last[chat_id] = time.monotonic()

throttle = ChatThrottle()

# блокировка скачивания на трек: защита от гонки двойного нажатия ▶
# (проигравший ждал и после победителя забирал файл из кеша file_id)
_dl_locks: dict[int, asyncio.Lock] = {}

def _lock_for(track_id: int) -> asyncio.Lock:
    lock = _dl_locks.get(track_id)
    if lock is None:
        lock = asyncio.Lock()
        _dl_locks[track_id] = lock
    return lock

async def send_preview_file(bot: Bot, chat_id: int, track: Track,
                            status_msg: Message | None = None) -> bool:
    """Последний шанс, когда полного аудио нет нигде: 30-сек фрагмент
    из Deezer/iTunes. Возвращает True, если фрагмент доставлен в чат."""
    import html as _html
    import re as _re
    import httpx
    from aiogram.types import BufferedInputFile
    from services.preview import best_preview

    artist, title = (track.artist or ""), (track.title or "")
    if not title.strip() and not artist.strip():
        return False
    if status_msg:
        try:
            await status_msg.edit_text("⚠ Полной версии нет в каталогах — ищу фрагмент (30 с)…")
        except Exception:
            pass
    try:
        url = await best_preview(artist, title)
    except Exception:
        url = None
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.content
        if not data or len(data) < 10_000:
            return False
        fname = _re.sub(r'[\\/:*?"<>|]+', "_", f"{artist} - {title}")[:70] + ".mp3"
        caption = (f"🎵 <b>{_html.escape(title or 'Без названия')}</b>"
                   + (f" — {_html.escape(artist)}" if artist else "") + "\n"
                   "⚠ Полной версии нет в доступных каталогах — 30-секундный фрагмент")
        await bot.send_audio(chat_id, BufferedInputFile(data, filename=fname),
                             caption=caption)
        return True
    except Exception:
        log.exception("preview send failed for track %s", track.id)
        return False

async def deliver_track(bot: Bot, chat_id: int, session: AsyncSession,
                        track: Track, quality: str | None = None,
                        status_msg: Message | None = None,
                        user_id: int | None = None) -> bool:
    """Полная доставка трека в чат: из кеша, локального пути или скачиванием."""
    quality = quality or settings.DEFAULT_QUALITY
    from services import ingest
    from services.catalog import meta_of

    if track.file_id and await send_track_cached(bot, chat_id, track):
        return True

    lock = _lock_for(track.id)
    if lock.locked():
        # кто-то уже качает этот трек: просто ждём и возьмём результат из кеша
        await status_msg.edit_text("⏳ Уже скачивается — подожди пару секунд…") \
            if status_msg else None
    async with lock:
        # свежая строка: другой поток мог уже скачать и заполнить file_id/local_path
        fresh = await catalog.get_track(session, track.id) or track
        if fresh.file_id and await send_track_cached(bot, chat_id, fresh):
            return True

        if fresh and fresh.local_path:
            p = Path(fresh.local_path)
            if p.exists() and p.stat().st_size > 0:
                fid = await send_audio_file(bot, chat_id, fresh, p)
                if fid:
                    await save_file_id_retry(fresh.id, fid, fresh.quality,
                                             p.stat().st_size, fresh.local_path)
                    return True
                p.unlink(missing_ok=True)

        async def on_progress(p: float):
            if status_msg:
                try:
                    await status_msg.edit_text(
                        f"⬇ Скачиваю: {track.artist} — {track.title}\n{int(p)}%",
                        reply_markup=None)
                except Exception:
                    pass

        try:
            res = await ingest.resolve(session, meta_of(track), quality,
                                       user_id=user_id, on_progress=on_progress)
            if res.status == "not_found" or not (res.file_path or res.track):
                # сторонние tg-боты не работают: Telegram запрещает bot→bot
                # сообщения (USER_BOT_TO_BOT_DISABLED) — только 30-сек фрагмент
                if await send_preview_file(bot, chat_id, track, status_msg):
                    return True
                return False

            target = res.track
            if res.file_path and res.track:
                try:
                    fid = await send_audio_file(bot, chat_id, target, res.file_path)
                except Exception as send_err:  # noqa: BLE001
                    # >50 MB (FLAC) не проходит облачный Bot API — качаем 320-битрайт
                    from aiogram.exceptions import TelegramEntityTooLarge
                    if not isinstance(send_err, TelegramEntityTooLarge) \
                            or quality == "aac320":
                        raise
                    log.info("track %s: >50 MB, ретрай в aac320", track.id)
                    fid = None
                    try:
                        res2 = await ingest.resolve(session, meta_of(track), "aac320",
                                                    user_id=user_id,
                                                    on_progress=on_progress)
                        if res2.file_path and res2.track:
                            fid = await send_audio_file(bot, chat_id, res2.track,
                                                        res2.file_path)
                            if fid:
                                target = res2.track
                                try:
                                    res.file_path.unlink()
                                except OSError:
                                    pass
                                res = res2
                        else:
                            raise send_err from None
                    except Exception:
                        log.exception("aac320 retry failed for track %s", track.id)
                if fid:
                    try:
                        size = res.file_path.stat().st_size
                    except OSError:
                        size = getattr(res, "size", 0) or 0
                    await save_file_id_retry(target.id, fid, res.quality,
                                             size, res.file_path.as_posix())
                try:
                    res.file_path.unlink()
                except OSError:
                    pass
                return bool(fid)

            # cached без file_id (файл был удалён) — повторно пробуем отдачу
            if res.track:
                track_after = await catalog.get_track(session, res.track.id)
                if track_after and await send_track_cached(bot, chat_id, track_after):
                    return True
            # сторонние tg-боты запрещены Telegram (bot→bot) — сразу превью
            if await send_preview_file(bot, chat_id, track, status_msg):
                return True
            return False
        except Exception:
            import logging
            logging.getLogger("bot.runtime").exception(
                "deliver_track failed for track %s", track.id)
            # файл мог остаться поломанным
            return False
