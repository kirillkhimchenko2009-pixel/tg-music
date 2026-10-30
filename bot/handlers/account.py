"""Подключение токенов сервисов: /token → выбор сервиса → ввод токена.
Для Яндекс Музыки есть АВТО-вход по Device Flow (OAuth): бот выдаёт ссылку и код,
юзер подтверждает вход — токен копировать не нужно."""
import asyncio
import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import runtime
from services.authstore import set_token

log = logging.getLogger("bot.account")
router = Router(name="account")

class TokStates(StatesGroup):
    waiting_token = State()

# (название, key): в authstore пишется только svc; "ym-auto" — это вариант получения
BTN_YM_TOKEN = "Яндекс Музыка 🎙 (пришлёт токен)"
BTN_YM_AUTO = "Яндекс Музыка 🎙 — вход по коду ✨"
BTN_ZVUK = "Звук 🔊 (FLAC)"

def _service_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_YM_AUTO, callback_data="tok:ym-auto")],
        [InlineKeyboardButton(text=BTN_YM_TOKEN, callback_data="tok:ym")],
        [InlineKeyboardButton(text=BTN_ZVUK, callback_data="tok:zvuk")],
        [InlineKeyboardButton(text="✖ Отмена", callback_data="tokcancel")],
    ])

@router.message(Command("token"))
async def cmd_token(message: types.Message, state: FSMContext):
    await message.answer(
        "Подключения аккаунтов для максимального качества и\n"
        "приватных плейлистов:\n\n"
        "• <b>Яндекс Музыка</b> — поиск/скачивание 320 & FLAC* "
        "(звук, страница yandex.ru/api — см. справку /token_help)\n"
        "• <b>Звук</b> — стрим FLAC\n\n"
        "Выбери сервис:", reply_markup=_service_kb())
    await state.clear()

@router.callback_query(F.data == "tokcancel")
async def cb_tokcancel(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отменено")

@router.callback_query(F.data.startswith("tok:"))
async def cb_tok(cb: types.CallbackQuery, state: FSMContext):
    svc = cb.data.split(":")[1]
    if svc == "ym-auto":
        await cb.answer("Запускаю вход по коду…")
        await _ym_device_flow(cb)
        return
    if svc in ("ym", "zvuk"):
        await state.set_state(TokStates.waiting_token)
        await state.update_data(service=svc)
        await cb.message.answer(
            "Пришли мне <b>токен</b> одним сообщением (инструкция — /token_help). "
            "Сохранится только для тебя.", reply_markup=None)
        await cb.answer()
        return
    await cb.answer("Сервис неизвестен", show_alert=True)


# ---------- Яндекс Музыка: авто-вход по Device Flow (OAuth) ----------

async def _ym_device_flow(cb: types.CallbackQuery):
    """Запрос OAuth-кода у Яндекса, ссылка+код юзеру, ожидание подтверждения,
    автоматическое сохранение access_token в authstore (service="ym")."""
    bot = cb.bot
    chat_id = cb.message.chat.id
    user_id = int(cb.from_user.id)
    state_msg = await bot.send_message(chat_id, "⏳ Запрашиваю код у Яндекс OAuth…")

    async def flow():
        try:
            from yandex_music import ClientAsync
            client = ClientAsync()

            def on_code(code):
                async def show():
                    try:
                        await state_msg.edit_text(
                            "1️⃣ Открой ссылку и войди в Яндекс:\n"
                            f"{code.verification_url}\n"
                            "2️⃣ Введи код:\n"
                            f"<code>{code.user_code}</code>\n\n"
                            "Жду подтверждения (до 15 минут)…")
                    except Exception:
                        pass
                asyncio.create_task(show())

            oauth = await asyncio.wait_for(client.device_auth(on_code=on_code),
                                           timeout=900)
            token = oauth.access_token
            async with runtime.get_session() as s:
                await set_token(s, user_id, "ym", token)
            try:
                await state_msg.edit_text(
                    "✅ <b>Яндекс Музыка подключена!</b>\n\n"
                    "Теперь: полный каталог (радио-классика, новые релизы), "
                    "онлайн-плеер играет треки целиком, скачивание 320/FLAC, "
                    "импорт твоих плейлистов ЯМ по ссылке.")
            except Exception:
                await bot.send_message(chat_id, "✅ Яндекс Музыка подключена!")
            log.info("ym token сохранён для uid=%s", user_id)
        except asyncio.TimeoutError:
            await state_msg.edit_text("⚠️ Вход не подтверждён за 15 минут. "
                                      "Попробуй ещё раз: /token")
        except Exception as e:
            log.exception("ym device flow упал")
            await state_msg.edit_text(
                f"⚠️ Не удалось войти через код ({type(e).__name__}).\n"
                "Попробуй вручную по /token_help → прислать токен командой /token")

    asyncio.create_task(flow())

@router.message(TokStates.waiting_token, F.text)
async def on_token(message: types.Message, state: FSMContext):
    data = await state.get_data()
    svc = data.get("service")
    token = (message.text or "").strip()
    if not token or len(token) < 8:
        await message.answer("Токен слишком короткий — пришли целиком.")
        return
    await message.bot.delete_message(message.chat.id, message.message_id)
    async with runtime.get_session() as s:
        await set_token(s, message.from_user.id, svc, token)
    await state.clear()
    name = {"ym": "Яндекс Музыка", "zvuk": "Звук"}.get(svc, svc)
    await message.answer(f"✅ Токен для <b>{name}</b> сохранён. Можешь делить ссылки на плейлисты {name}!")

@router.message(Command("token_help"))
async def token_help(message: types.Message):
    await message.answer(
        "<b>Как подключить Яндекс Музыку</b>\n\n"
        "✨ <b>Способ 1 (лучший): в самом боте</b>\n"
        "<code>/token</code> → «Яндекс Музыка — вход по коду».\n"
        "Бот пришлёт ссылку и код: открой ссылку, введи код у Яндекса — "
        "всё, токен сохранится сам.\n\n"
        "🔗 <b>Способ 2: ссылка OAuth</b>\n"
        "Открой <a href='https://oauth.yandex.ru/authorize?response_type=token&amp;client_id=23cabbbdc6cd418abb4b39c32c41195d'>"
        "OAuth-ссылку Яндекс.Музыки</a>, разреши доступ; браузер перекинет на "
        "<code>music.yandex.ru/#access_token=…</code> — скопируй всё после "
        "<code>=</code> до следующего <code>&amp;</code> и пришли её в бот "
        "(<code>/token</code> → «пришлёт токен»). Если адрес мгновенно "
        "перекидывает — открой в браузере DevTools (F12) → Network → "
        "задержи скорость сети, чтобы успеть скопировать строку.\n\n"
        "<b>Звук (zvuk.com)</b>\n"
        "1. Войди на site\n"
        "2. F12 → Application → Cookies → <code>auth-token</code>\n\n"
        "Токен хранится только в БД (Postgres) и используется только для тебя.")
