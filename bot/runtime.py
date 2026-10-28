"""Рантайм-помощники: пользователи, отправка треков с кешем file_id и троттлингом."""
import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot
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

async def send_track_cached(bot: Bot, chat_id: int, track: Track) -> bool:
    """Быстрая отправка трека из кеша file_id. False, если кеша нет."""
    if not track.file_id:
        return False
    from bot.keyboards import action_kb
    try:
        in_fav = await _fav_state(chat_id, track.id)
        from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
        for attempt in range(3):
            try:
                await bot.send_audio(chat_id, track.file_id,
                                     title=track.title or "Трек",
                                     performer=track.artist or None,
                                     reply_markup=action_kb(track.id, in_fav))
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
    except Exception:
        # файл пропал/невалиден — чистим кеш
        await save_file_id_retry(track.id, "")
        return False

async def send_audio_file(bot: Bot, chat_id: int, track: Track, path) -> str | None:
    """Загрузка уже скачанного файла в ТГ c обложкой; возвращает file_id."""
    cover = None
    try:
        from pathlib import Path
        if track and track.cover_url:
            cover_path = settings.COVERS_DIR / f"{track.id}.jpg"
            if not cover_path.exists():
                import httpx
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                    r = await c.get(track.cover_url)
                    r.raise_for_status()
                    cover_path.write_bytes(r.content)
            cover = FSInputFile(cover_path)
    except Exception:
        cover = None

    from bot.keyboards import action_kb
    in_fav = await _fav_state(chat_id, track.id)
    msg = None
    from aiogram.exceptions import (TelegramNetworkError, TelegramRetryAfter,
                                    TelegramEntityTooLarge)
    for attempt in range(3):
        try:
            msg = await bot.send_audio(
                chat_id, FSInputFile(path),
                title=track.title or "Трек", performer=track.artist or None,
                thumbnail=cover,
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
    audio = msg.audio
    return (audio.file_id if audio else None)

async def get_or_create_playlist(session: AsyncSession, user_id: int, title: str) -> Playlist:
    pl = (await session.execute(select(Playlist).where(
        Playlist.owner_id == user_id, Playlist.title == title))).scalar_one_or_none()
    if pl:
        return pl
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
            return False
        except Exception:
            import logging
            logging.getLogger("bot.runtime").exception(
                "deliver_track failed for track %s", track.id)
            # файл мог остаться поломанным
            return False
