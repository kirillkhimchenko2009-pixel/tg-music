"""Подписка Vibe в боте: pre_checkout_query + successful_payment.

Платежи Telegram-звёздами (XTR): счёт создаёт сервер (/api/billing/checkout
→ createInvoiceLink), открытие — WebApp.openInvoice. Подтверждение приходит
сюда: pre_checkout отвечаем сразу, successful_payment — зачисляем подписку
в общую БД (services/billing.grant, идемпотентно по charge_id).
"""
import logging

from aiogram import Router, types

from bot import runtime
from core.config import settings
from services.billing import (PAYLOAD_PREFIX, PLAN_DAYS, TERM_TITLES,
                              TIERS, grant, parse_plan)

router = Router(name="billing")
log = logging.getLogger("bot.billing")


def _parse_payload(payload: str) -> tuple[str, str, int]:
    """vibe-sub:<tier>-<term>:<uid> → (tier, term, uid).
    Легаси vibe-sub:<term>:<uid> → pro. Неизвестное → ('pro', 'month', 0)."""
    parts = str(payload or "").split(":")
    if len(parts) == 3 and parts[0] == PAYLOAD_PREFIX:
        tier, term = parse_plan(parts[1])
        try:
            return (tier, term, int(parts[2]))
        except ValueError:
            return ("pro", "month", 0)
    return ("pro", "month", 0)


@router.pre_checkout_query()
async def pre_checkout(q: types.PreCheckoutQuery):
    # Валидность payload/суммы гарантирует Telegram (счёт создан нами);
    # отвечаем ok мгновенно, чтобы платёж не завис на 10 секунд.
    tier, term, _uid = _parse_payload(q.invoice_payload)
    ok = tier in TIERS and term in PLAN_DAYS
    await q.answer(ok=ok, error_message="Счёт устарел — создай заново в приложении." if not ok else None)


@router.message(lambda m: m.successful_payment is not None)
async def successful_payment(message: types.Message):
    sp = message.successful_payment
    tier, term, uid = _parse_payload(sp.invoice_payload)
    if not uid:  # счёт не из мини-аппа — платил сам пользователь в чате
        uid = message.from_user.id if message.from_user else 0
    amount = int(sp.total_amount or 0)          # звёзды (XTR — целые)
    charge = str(sp.telegram_payment_charge_id or "")
    log.info("billing: успешный платёж %s★ %s-%s uid=%s", amount, tier, term, uid)
    async with runtime.get_session() as s:
        res = await grant(s, uid, tier, term, charge, amount,
                          payload=sp.invoice_payload or "")
    exp = (res.get("expires_at") or "")[:10]
    tier_name = TIERS.get(tier, TIERS["pro"])["name"]
    term_word = {"month": "31 день", "half": "183 дня", "year": "366 дней"}.get(term, "")
    await message.answer(
        f"🌟 <b>{tier_name} активна!</b>\n"
        f"Срок: <b>{TERM_TITLES[term]}</b> ({term_word} звёздами: {amount}★).\n"
        f"Действует до: <b>{exp}</b>.\n\n"
        "Спасибо за поддержку! 💜")


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
        "🌟 <b>Подписка Vibe</b> — два тира, оплата звёздами:\n\n"
        "💙 <b>Vibe Lite</b> — 25★/мес, 120★/полгода, 200★/год\n"
        "Безлимитные скачивания треков в чат.\n\n"
        "💜 <b>Vibe Pro</b> — 50★/мес, 250★/полгода, 450★/год\n"
        "Всё из Lite + PRO-обложка плеера, приоритет волны и очереди.\n\n"
        "Оплата — в мини-приложении, раздел <b>Vibe Pro</b>.", reply_markup=kb)
