"""Команды /start, /help, /id + диплинки инлайн-карточек (dl_<track_id>)."""
import asyncio
import logging
from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject, CommandStart
from sqlalchemy import select as _select

from core.config import settings
from bot import runtime
from bot.keyboards import main_menu

router = Router(name="start")
log = logging.getLogger("bot.start")

_HELP_TEXT = (
    "<b>🎧 Музыкальный бот</b>\n\n"
    "🔍 Пришли название трека или исполнителя — покажу варианты\n"
    "🔗 Кинь <b>ссылку на плейлист</b> (Яндекс Музыка, Spotify, Звук, YouTube) — скачаю всё\n"
    "➕ <code>/get Название — Исполнитель</code> — скачать конкретный трек\n"
    "💬 В любом чате: <code>@имя_бота + трек</code> — инлайн-поиск карточками\n"
    "📋 <code>/newplaylist Название</code> — создать свой плейлист\n"
    "🎵 <code>/playlists</code> — мои плейлисты\n"
    "🔑 <code>/token</code> — подключить Яндекс Музыку / Звук (FLAC-качество)\n"
    "🌐 <code>/code</code> — код для входа в приложение из браузера\n"
    "📎 Пришли <b>txt-файл</b> со списком треков — скачаю все\n\n"
    "📱 Открой <b>мини-приложение</b> для полноценного плеера — кнопка ниже."
)


def _banner() -> str | None:
    """Баннер приветствия из assets/ (расширение зависит от сборки)."""
    assets = Path(__file__).resolve().parent.parent / "assets"
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = assets / f"banner{ext}"
        if p.exists():
            return str(p)
    return None


async def _deliver_from_start(message: types.Message, track_id: int) -> None:
    """Диплинк t.me/<bot>?start=dl_<id> из инлайн-карточки: бот качает трек
    прямо в чат, откуда нажали «Скачать»."""
    from services import catalog
    status = await message.answer("⬇ Скачиваю…")
    bot, chat_id, uid = message.bot, message.chat.id, message.from_user.id

    async def _job():
        try:
            async with runtime.get_session() as s:
                track = await catalog.get_track(s, track_id)
                if not track:
                    await status.edit_text("Трек не найден в каталоге.")
                    return
                ok = await runtime.deliver_track(bot, chat_id, s, track,
                                                 status_msg=status, user_id=uid)
            if ok:
                try:
                    await status.delete()
                except Exception:
                    pass
            else:
                await status.edit_text(
                    "❌ Не удалось скачать — попробуй другой вариант.")
        except Exception:  # noqa: BLE001
            log.exception("dl_%s из диплинка не скачался", track_id)
            try:
                await status.edit_text("❌ Ошибка скачивания — попробуй позже.")
            except Exception:
                pass

    asyncio.create_task(_job())   # фон: бот остаётся отзывчивым


@router.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, message.from_user.id,
                                  message.from_user.username,
                                  message.from_user.first_name or "")
    arg = (command.args or "").strip()
    if arg.startswith("dl_"):
        try:
            await _deliver_from_start(message, int(arg[3:]))
        except ValueError:
            await message.answer("Ссылка устарела — найди трек заново.")
        return
    if arg.startswith("ref"):
        # рефералка: t.me/<bot>?start=ref<inviter_uid>
        await _handle_referral(message, arg[3:])
        return
    if arg == "landing":
        # переход с лендинга (docs/index.html) — считаем в wave_kv
        try:
            from core.models import WaveKV
            async with runtime.get_session() as s:
                row = (await s.execute(_select(WaveKV).where(
                    WaveKV.key == "landing_visits"))).scalar_one_or_none()
                n = int((row.value or {}).get("n", 0)) + 1 if row else 1
                if row:
                    row.value = {"n": n}
                else:
                    s.add(WaveKV(key="landing_visits", value={"n": n}))
                await s.commit()
            log.info("переход с лендинга #%s (uid=%s)", n, message.from_user.id)
        except Exception:
            log.exception("landing visit count fail")
        await _greet(message)
        return
    await _greet(message)      # start=inline (кнопка инлайна) тоже сюда


async def _handle_referral(message: types.Message, inviter_raw: str) -> None:
    """Пришёл по ссылке друга: записать инвайт, наградить пригласившего.

    Награда — 3 дня Vibe Lite пригласившему (бесплатное привлечение).
    Защита: один invited_id = один инвайт; сам себя не приглашает; гостевые
    uid (1e12+) в боте не существуют — юзер всегда телеграмный."""
    import html as _html
    try:
        inviter_id = int(inviter_raw)
    except ValueError:
        await _greet(message)
        return
    invited_id = message.from_user.id
    if inviter_id == invited_id or inviter_id <= 0:
        await _greet(message)
        return
    from core.models import Referral
    from services import billing as _billing
    msg_text = None
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, invited_id,
                                  message.from_user.username,
                                  message.from_user.first_name or "")
        dup = (await s.execute(_select(Referral).where(
            Referral.invited_id == invited_id))).scalar_one_or_none()
        if dup is None:
            s.add(Referral(inviter_id=inviter_id, invited_id=invited_id,
                           rewarded=True))
            await s.commit()
            try:
                await _billing.grant(s, inviter_id, "lite", "ref3",
                                     charge_id=f"ref:{inviter_id}:{invited_id}",
                                     amount=0)
                msg_text = "ok"
            except Exception:
                log.exception("referral reward fail")
    await _greet(message)
    if msg_text and inviter_id != invited_id:
        try:
            await message.bot.send_message(
                inviter_id,
                f"🎁 Друг {_html.escape(message.from_user.first_name or 'новый пользователь')} "
                f"запустил бота по твоей ссылке — <b>+3 дня Vibe Lite!</b>\n"
                f"Приглашай ещё: каждый друг = +3 дня.")
        except Exception:
            pass


@router.message(Command("ref"))
async def cmd_ref(message: types.Message) -> None:
    """🎁 Пригласить друга: ссылка + статистика + кнопка «Поделиться».
    Каждый запустивший по ссылке = +3 дня Vibe Lite пригласившему."""
    from urllib.parse import quote
    from core.models import Referral, User
    uid = message.from_user.id
    async with runtime.get_session() as s:
        me = (await s.execute(_select(User).where(User.id == uid))).scalar_one_or_none()
        invited = list((await s.execute(_select(Referral).where(
            Referral.inviter_id == uid))).scalars())
    uname = settings.BOT_USERNAME or (await _bot_username(message.bot))
    link = f"https://t.me/{uname}?start=ref{uid}"
    n = len(invited)
    days = n * 3
    txt = (
        "<b>🎁 Пригласи друга — получи Vibe Lite</b>\n\n"
        f"Твоя ссылка:\n<code>{link}</code>\n\n"
        f"Каждый, кто запустит бота по ней — <b>+3 дня Vibe Lite</b> тебе.\n"
        f"10 друзей = месяц Lite бесплатно.\n\n"
        f"📊 Приглашено: <b>{n}</b> → заработано <b>{days} дн.</b>"
    )
    share = (f"https://t.me/share/url?url={quote(link)}"
             f"&text={quote('Слушаю и качаю музыку тут 🎧 без рекламы и блюра')}")
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться ссылкой",
                              url=share)],
    ])
    await message.answer(txt, reply_markup=kb)


async def _bot_username(bot) -> str:
    """Кэш username бота (для реф-ссылок), если не задан в env."""
    global _BOT_UNAME
    if _BOT_UNAME:
        return _BOT_UNAME
    try:
        me = await bot.get_me()
        _BOT_UNAME = me.username or ""
    except Exception:
        _BOT_UNAME = ""
    return _BOT_UNAME


_BOT_UNAME = ""


async def _greet(message: types.Message) -> None:
    """Приветствие: баннер-фото с полным текстом в подписи (фолбэк — текст)."""
    banner = _banner()
    if banner:
        from aiogram.types import FSInputFile
        try:
            await message.answer_photo(
                FSInputFile(banner, chunk_size=1 << 20),
                caption=_HELP_TEXT, reply_markup=main_menu())
        except Exception:  # noqa: BLE001
            log.exception("баннер приветствия не отправился — текстом")
            await message.answer(_HELP_TEXT, reply_markup=main_menu())
    else:
        await message.answer(_HELP_TEXT, reply_markup=main_menu())
    from bot.keyboards import main_reply
    rk = main_reply()
    if rk:
        await message.answer("Кнопки для команд всегда под рукой 👇", reply_markup=rk)

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await _greet(message)

@router.message(Command("id"))
async def cmd_id(message: types.Message):
    await message.answer(f"Твой id: <code>{message.from_user.id}</code>")

@router.message(F.reply_to_message)
async def ignore_replies(message: types.Message):
    return
