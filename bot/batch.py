"""Массовая обработка списка треков (из ссылки на плейлист, txt-файла или сообщения списком).

Пачка любого размера ставится в очередь Neon целиком (Релиз 2.5): воркеры
качают параллельно с резервом Render, файлы приходят в чат по мере
готовности, бот ведёт счётчик прогресса и умеет отменять незабранные джобы."""
import asyncio
import logging
import time

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot import runtime
from bot.state import EXT_CACHE
from core.models import Playlist, PlaylistTrack
from services import catalog
from services.providers.base import TrackMeta

router = Router(name="batch")
log = logging.getLogger("bot.batch")

# занятые юзеры и их задачи; CANCEL — флаг-запрос на остановку
BUSY: set[int] = set()
CANCEL: set[int] = set()
TASKS: dict[int, asyncio.Task] = {}
# id джоб текущего батча юзера (для отмены 'new' джоб в очереди)
BATCH_JOBS: dict[int, list[str]] = {}


def external_kb(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⬇ Скачать всё ({count}) в ТГ",
                              callback_data="extdl")],
        [InlineKeyboardButton(text="❤ В любимое всё",
                              callback_data="extfav"),
         InlineKeyboardButton(text="➕ В мой плейлист",
                              callback_data="extpl")],
        [InlineKeyboardButton(text="✖ Отмена", callback_data="extcancel")],
    ])


def _fmt_eta(sec: float) -> str:
    sec = int(sec)
    if sec < 90:
        return f"≈{sec} с"
    m = sec // 60
    if m < 90:
        return f"≈{m} мин"
    h, mm = divmod(m, 60)
    return f"≈{h} ч {mm:02d} мин"


async def start_batch(message: types.Message, title: str, metas: list[TrackMeta]) -> None:
    """Кладёт список в кеш и показывает сводку с кнопками.

    Виджет-карточка (16.11): если у треков есть обложка — сводка приходит
    ФОТО с подписью и кнопками (красивее текста); обложка битая/приватная
    → обычный текст. Плюс 🔥-реакция на принятое сообщение."""
    from bot import fx
    EXT_CACHE[message.from_user.id] = {"title": title, "metas": metas}
    n = len(metas)
    head = ", ".join(t.display() for t in metas[:3])
    caption = (f"<b>{fx.esc_html(title)}</b>\nТреков: <b>{n}</b>\n\n"
               f"Примеры: {fx.esc_html(head)}…\nЧто делаем?")
    kb = external_kb(n)
    cover = next((m.cover_url for m in metas if m.cover_url), None)
    if cover:
        try:
            await message.answer_photo(photo=cover, caption=caption,
                                       reply_markup=kb)
            await fx.react(message.bot, message.chat.id,
                           message.message_id, fx.FIRE)
            return
        except Exception as e:  # noqa: BLE001
            log.info("обложка %s не отправилась (%s) — текстом",
                     (cover or "")[:60], type(e).__name__)
    await message.answer(caption, reply_markup=kb)
    await fx.react(message.bot, message.chat.id, message.message_id, fx.FIRE)


async def run_batch(bot, chat_id: int, user_id: int, metas: list[TrackMeta],
                    quality: str) -> tuple[int, list[str]]:
    """Пачка → очередь Neon (Релиз 2.5): бот только ставит джобы и следит.

    Скачивание и отправку файлов в чат выполняют воркеры (Botkeep →
    serv00 → резерв Render), поэтому APP-хост не тянет yt-dlp в память
    и пачки любого размера не блокируют друг друга. Прогресс — счётчик
    статусов всех джоб батча; отмена (cb_extstop) = 'canceled' для ещё
    не забранных джоб. Возвращает (готово, [неудавшиеся]) — контракт
    прежний.
    """
    from music import mediajobs

    total = len(metas)
    stop_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖ Остановить", callback_data="extstop")]])
    msg = await bot.send_message(
        chat_id, f"⬇ Принял: <b>{total}</b> треков\n"
                 "Смотрю, что уже скачано…",
        reply_markup=stop_kb)

    # --- постановка в очередь: дедуп по активной джобе, по одной джобе за раз
    # BATCH_JOBS ссылается на ЖИВОЙ список: отмена видна даже во время постановки
    job_ids: list[str] = []
    BATCH_JOBS[user_id] = job_ids
    jobs_meta: dict[str, TrackMeta] = {}      # job_id → метаданные (для сводки)
    skipped = 0                               # уже скачаны ранее (кеш file_id)
    t0 = time.monotonic()
    try:
        async with runtime.get_session() as session:
            for meta in metas:
                if user_id in CANCEL:
                    break
                track = await catalog.upsert_track(session, meta)
                await session.commit()
                # уже скачан ранее и лежит в кеше Telegram (file_id) —
                # повторно не качаем (fix 16.11: очередь ставила джобы на все)
                if getattr(track, "file_id", None):
                    skipped += 1
                    continue
                active = await mediajobs.find_active(session, track.id, user_id)
                if active is not None:
                    job_ids.append(active.id)
                    jobs_meta[active.id] = meta
                    continue
                job = await mediajobs.create_job(
                    session, track_id=track.id, uid=user_id,
                    quality=quality, source=track.source)
                await session.commit()
                job_ids.append(job.id)
                jobs_meta[job.id] = meta
    except Exception:  # noqa: BLE001
        log.exception("батч %s: постановка в очередь упала", user_id)

    queued = len(job_ids)                    # реально ждём только новые
    if skipped:
        try:
            await msg.edit_text(
                f"⬇ Уже скачано: <b>{skipped}</b> — пропускаю\n"
                f"В очередь: <b>{queued}</b> новых треков."
                if queued else
                f"✅ Все <b>{skipped}</b> треков уже скачаны — повторно не качаю")
        except Exception:  # noqa: BLE001
            pass

    # --- вотчер: статусы всех джоб одним запросом, правка не чаще 5 с
    sent, settled, stopped = 0, 0, False
    errored, canceled = 0, 0
    failed: list[str] = []
    deadline = time.monotonic() + max(45 * 60.0, 150.0 * max(queued, 1))
    last_edit = 0.0
    st_map: dict[str, str] = {}
    while job_ids and time.monotonic() < deadline:
        if user_id in CANCEL:
            stopped = True
            break
        await asyncio.sleep(2.5)
        try:
            async with runtime.get_session() as session:
                st_map = await mediajobs.statuses_of(session, job_ids)
        except Exception:  # noqa: BLE001
            continue                    # БД/сеть моргнули — вотчер живёт
        done = sum(1 for v in st_map.values() if v == "done")
        errored = sum(1 for v in st_map.values() if v == "error")
        canceled = sum(1 for v in st_map.values() if v == "canceled")
        new_settled = done + errored + canceled
        if new_settled == settled:
            continue
        settled = new_settled
        now = time.monotonic()
        if now - last_edit < 5.0 and settled < queued:
            continue
        last_edit = now
        eta = (queued - settled) * (now - t0) / max(settled, 1)
        extra = f" · ⏹ {canceled}" if canceled else ""
        skip_note = f" · ⏭ {skipped} из кеша" if skipped else ""
        tail = f"\nОсталось {_fmt_eta(eta)}" if settled < queued else ""
        try:
            await msg.edit_text(
                f"⬇ Готово: <b>{done}</b>/{queued} · ⛔ {errored}"
                f"{extra}{skip_note}{tail}",
                reply_markup=stop_kb if settled < queued else None)
        except Exception:  # noqa: BLE001
            pass
        if settled >= queued:
            break

    # финальный снапшот (после отмены/дедлайна статусы могли догнать)
    if job_ids:
        try:
            async with runtime.get_session() as session:
                st_map = await mediajobs.statuses_of(session, job_ids)
        except Exception:  # noqa: BLE001
            pass
    sent = sum(1 for v in st_map.values() if v == "done")
    errored = sum(1 for v in st_map.values() if v == "error")
    canceled = sum(1 for v in st_map.values() if v == "canceled")
    settled = sent + errored + canceled
    failed = [jobs_meta[jid].display() for jid in job_ids
              if st_map.get(jid) == "error" and jid in jobs_meta]

    if queued == 0 and skipped and not stopped:
        pass                            # сводка «все уже скачаны» уже отправлена
    elif queued == 0:
        try:
            await msg.edit_text("⚠️ Не удалось поставить треки в очередь")
        except Exception:  # noqa: BLE001
            pass
    elif stopped:
        try:
            await msg.edit_text(
                f"⏹ Остановлено\n\nОтправлено: <b>{sent}</b>"
                f" · отменено: {canceled} · не удалось: {errored}"
                + (f" · из кеша: {skipped}" if skipped else ""),
                reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
    else:
        tail = ""
        if failed:
            tail = ("\n\n<b>Не удалось:</b>\n"
                    + "\n".join(f"• {s}" for s in failed[:15]))
            if len(failed) > 15:
                tail += f"\n… +{len(failed) - 15}"
        skip_note = f" · из кеша: <b>{skipped}</b>" if skipped else ""
        extra = "\nФайлы продолжают приходить в чат" if settled < queued else ""
        try:
            await msg.edit_text(
                f"✅ Готово: {sent}/{queued}{skip_note}{extra}{tail}",
                reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
    from bot import fx
    if errored:
        await fx.react(bot, chat_id, msg.message_id, fx.SAD)
    BATCH_JOBS.pop(user_id, None)
    CANCEL.discard(user_id)
    return sent, failed


async def _cancel_batch_jobs(uid: int) -> None:
    """Отмена батча в очереди: 'new' → 'canceled' (воркер их уже не возьмёт).
    Leased доделываются — файлы за ними уже в пути."""
    from music import mediajobs
    ids = BATCH_JOBS.get(uid) or []
    if not ids:
        return
    try:
        async with runtime.get_session() as session:
            n = await mediajobs.cancel_jobs(session, ids)
        if n:
            log.info("батч %s: отменено джоб в очереди: %s", uid, n)
    except Exception:  # noqa: BLE001
        log.exception("батч %s: отмена джоб в очереди не удалась", uid)


def spawn_batch(user_id: int, bot, chat_id: int, metas: list[TrackMeta],
                quality: str) -> bool:
    """Фон-запуск run_batch; False, если у юзера уже идёт загрузка."""
    if user_id in BUSY:
        return False
    BUSY.add(user_id)
    CANCEL.discard(user_id)

    async def _job():
        try:
            await run_batch(bot, chat_id, user_id, metas, quality)
        except asyncio.CancelledError:
            try:
                await bot.send_message(chat_id, "⏹ Загрузка остановлена")
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            log.exception("run_batch упал для %s", user_id)
        finally:
            BUSY.discard(user_id)
            TASKS.pop(user_id, None)
            CANCEL.discard(user_id)
            BATCH_JOBS.pop(user_id, None)

    TASKS[user_id] = asyncio.create_task(_job())
    return True


@router.callback_query(F.data == "extdl")
async def cb_extdl(cb: types.CallbackQuery):
    data = EXT_CACHE.get(cb.from_user.id)
    if not data:
        await cb.answer("Список устарел", show_alert=True)
        return
    from core.config import settings
    if not spawn_batch(cb.from_user.id, cb.bot, cb.message.chat.id,
                       list(data["metas"]), settings.DEFAULT_QUALITY):
        await cb.answer("Уже качаю список — дождись завершения", show_alert=True)
        return
    await cb.answer("⬇ Качаю в фоне — прогресс придёт сообщениями")


@router.callback_query(F.data == "extfav")
async def cb_extfav(cb: types.CallbackQuery):
    """Весь список → системный плейлист «❤ Любимое» (без скачивания).

    Треки сначала попадают в каталог (upsert), затем одним INSERT-батчем
    в «Любимое»: N коммитов по треку на Neon медленны и дают «database
    is locked». Уже лайкнутые пропускаются."""
    data = EXT_CACHE.get(cb.from_user.id)
    if not data:
        await cb.answer("Список устарел", show_alert=True)
        return
    uid = cb.from_user.id
    from core.models import PlaylistTrack
    added = 0
    try:
        async with runtime.get_session() as s:
            await runtime.ensure_user(s, uid, cb.from_user.username,
                                      cb.from_user.first_name or "")
            pl = await runtime.get_favorites_playlist(s)
            have = set((await s.execute(
                select(PlaylistTrack.track_id).where(
                    PlaylistTrack.playlist_id == pl.id))).scalars())
            pos = len(have)
            for meta in data["metas"]:
                track = await catalog.upsert_track(s, meta)
                await s.flush()
                if track.id in have:
                    continue
                have.add(track.id)
                s.add(PlaylistTrack(playlist_id=pl.id, track_id=track.id,
                                    position=pos))
                pos += 1
                added += 1
            await s.commit()
    except Exception:  # noqa: BLE001
        log.exception("extfav %s: добавление в любимое упало", uid)
        await cb.answer("Не получилось — попробуй ещё раз", show_alert=True)
        return
    await cb.answer("❤ Добавляю…" if added else "Всё уже в любимом")
    tail = "" if added == len(data["metas"]) else "\nОстальные уже были в любимом"
    ack = await cb.message.answer(
        f"❤ В «{runtime.FAVORITES_TITLE}» добавлено треков: <b>{added}</b>{tail}")
    from bot import fx
    await fx.react(cb.bot, cb.message.chat.id, ack.message_id, fx.HEART)


@router.callback_query(F.data == "extstop")
async def cb_extstop(cb: types.CallbackQuery):
    uid = cb.from_user.id
    if uid not in BUSY:
        await cb.answer("Загрузка уже не идёт")
        return
    CANCEL.add(uid)
    # 'new'-джобы батча отменяем в очереди сразу (воркер их не возьмёт);
    # отдельной задачей — вотчер может умереть раньше ответа БД
    asyncio.create_task(_cancel_batch_jobs(uid))
    t = TASKS.get(uid)
    if t and not t.done():
        t.cancel()
    await cb.answer("⏹ Останавливаю…")


@router.callback_query(F.data == "extpl")
async def cb_extpl(cb: types.CallbackQuery):
    data = EXT_CACHE.get(cb.from_user.id)
    if not data:
        await cb.answer("Список устарел", show_alert=True)
        return
    async with runtime.get_session() as s:
        playlists = list((await s.execute(
            select(Playlist).where(Playlist.owner_id == cb.from_user.id))).scalars())
    if not playlists:
        await cb.answer("Сначала создай плейлист: /newplaylist Название",
                        show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=p.title, callback_data=f"extple:{p.id}")]
        for p in playlists
    ] + [[InlineKeyboardButton(text="✖", callback_data="extcancel")]])
    await cb.answer("Куда добавить?")
    await cb.message.answer("Выбери плейлист:", reply_markup=kb)


@router.callback_query(F.data.startswith("extple:"))
async def cb_extple(cb: types.CallbackQuery):
    data = EXT_CACHE.get(cb.from_user.id)
    pl_id = int(cb.data.split(":")[1])
    if not data:
        await cb.answer("Список устарел", show_alert=True)
        return
    added = 0
    async with runtime.get_session() as s:
        for meta in data["metas"]:
            track = await catalog.upsert_track(s, meta)
            await runtime.playlist_add_track(s, pl_id, track.id)
            added += 1
        pl = await s.get(Playlist, pl_id)
    title = pl.title if pl else "плейлист"
    await cb.answer()
    await cb.message.answer(
        f"✅ В плейлист «{title}» добавлено треков: <b>{added}</b>")


@router.callback_query(F.data == "extcancel")
async def cb_extcancel(cb: types.CallbackQuery):
    EXT_CACHE.pop(cb.from_user.id, None)
    await cb.answer("Отменено")
    try:
        await cb.message.delete()
    except Exception:
        pass
