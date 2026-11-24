"""Подписка Vibe Pro в боте: pre_checkout_query + successful_payment.

Платежи Telegram-звёздами (XTR): счёт создаёт сервер (/api/billing/checkout
→ createInvoiceLink), открытие — WebApp.openInvoice. Подтверждение приходит
сюда: pre_checkout отвечаем сразу, successful_payment — зачисляем подписку
в общую БД (services/billing.grant, идемпотентно по charge_id).
"""
import logging

from aiogram import Router, types

from bot import runtime
from core.config import settings
from services.billing import PAYLOAD_PREFIX, PLANS, grant

router = Router(name="billing")
log = logging.getLogger("bot.billing")


def _parse_payload(payload: str) -> tuple[str, int]:
    """vibe-sub:<plan>:<uid> → (plan, uid); неизвестный формат → ('month', 0)."""
    parts = str(payload or "").split(":")
    if len(parts) == 3 and parts[0] == PAYLOAD_PREFIX:
        try:
            return (parts[1] if parts[1] in PLANS else "month", int(parts[2]))
        except ValueError:
            pass
    return ("month", 0)


@router.pre_checkout_query()
async def pre_checkout(q: types.PreCheckoutQuery):
    # Валидность payload/суммы гарантирует Telegram (счёт создан нами);
    # отвечаем ok мгновенно, чтобы платёж не завис на 10 секунд.
    plan, _uid = _parse_payload(q.invoice_payload)
    ok = plan in PLANS
    await q.answer(ok=ok, error_message="Счёт устарел — создай заново в приложении." if not ok else None)


@router.message(lambda m: m.successful_payment is not None)
async def successful_payment(message: types.Message):
    sp = message.successful_payment
    plan, uid = _parse_payload(sp.invoice_payload)
    if not uid:  # счёт не из мини-аппа — платил сам пользователь в чате
        uid = message.from_user.id if message.from_user else 0
    amount = int(sp.total_amount or 0)          # звёзды (XTR — целые)
    charge = str(sp.telegram_payment_charge_id or "")
    log.info("billing: успешный платёж %s★ план=%s uid=%s", amount, plan, uid)
    async with runtime.get_session() as s:
        res = await grant(s, uid, plan, charge, amount, payload=sp.invoice_payload or "")
    exp = res.get("expires_at") or ""
    days_word = {"month": "31 день", "half": "183 дня", "year": "366 дней"}.get(plan, "")
    await message.answer(
        "🌟 <b>Vibe Pro активна!</b>\n"
        f"План: <b>{PLANS.get(plan, PLANS['month'])[2]}</b> ({days_word} звёздами: {amount}★).\n"
        f"Действует до: <b>{exp[:10]}</b>.\n\n"
        "Безлимитные скачивания в чат, PRO-обложка плеера и приоритет волны. Спасибо за поддержку! 💜")


@router.message(lambda m: m.text is not None and m.text.strip() == "/pro")
async def pro_cmd(message: types.Message):
    """Краткая справка о подписке + кнопка мини-приложения."""
    from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                               WebAppInfo)
    kb = None
    if settings.WEBAPP_URL:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎧 Оплатить в приложении",
                                 web_app=WebAppInfo(url=settings.WEBAPP_URL))
        ]])
    await message.answer(
        "🌟 <b>Vibe Pro</b> — подписка за звёзды:\n"
        "· 50★ — месяц\n· 250★ — полгода (выгода 15%)\n· 450★ — год (выгода 25%)\n\n"
        "Безлимитные скачивания в чат, PRO-обложка плеера, приоритет волны.\n"
        "Оплата — в мини-приложении, раздел <b>Vibe Pro</b>.", reply_markup=kb)
