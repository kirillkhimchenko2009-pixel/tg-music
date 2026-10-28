"""Подключение токенов сервисов: /token → выбор сервиса → ввод токена."""
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import runtime
from services.authstore import set_token

router = Router(name="account")

class TokStates(StatesGroup):
    waiting_token = State()

SERVICES = {
    "ym": ("Яндекс Музыка 🎙", "ym"),
    "zvuk": ("Звук 🔊 (FLAC)", "zvuk"),
}

def _service_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"tok:{svc}")]
        for svc, (name, _) in SERVICES.items()
    ] + [[InlineKeyboardButton(text="✖ Отмена", callback_data="tokcancel")]])

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
    if svc not in SERVICES:
        await cb.answer("Сервис неизвестен", show_alert=True)
        return
    await state.set_state(TokStates.waiting_token)
    await state.update_data(service=svc)
    await cb.message.answer(
        "Пришли мне <b>токен</b> одним сообщением (инструкция — /token_help). "
        "Сохранится только для тебя.", reply_markup=None)
    await cb.answer()

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
    name = SERVICES[svc][0]
    await message.answer(f"✅ Токен для <b>{name}</b> сохранён. Можешь делить ссылки на плейлисты {name}!")

@router.message(Command("token_help"))
async def token_help(message: types.Message):
    await message.answer(
        "<b>Как получить токен Яндекс Музыки</b>\n"
        "1. Открой <a href='https://music.yandex.ru/account'>music.yandex.ru/account</a>\n"
        "2. F12 → Application → Cookies → music.yandex.ru → <code>Session_id</code>\n"
        "Или из мобильного приложения через хранилище (инструкции в сети).\n\n"
        "<b>Звук (zvuk.com)</b>:\n"
        "1. Войди на site\n"
        "2. F12 → Application → Cookies → <code>auth-token</code>\n\n"
        "Токен хранится в БД на твоей машине (или VPS). Никому не отправляй его.")
