"""Биллинг мини-аппа: планы, статус подписки, чекаут за Telegram-звёзды."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bot import runtime
from core.config import settings
from server.auth import CurrentUser, is_guest_id, tg_user
from services import billing

LOG = logging.getLogger("routes.billing")
router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutIn(BaseModel):
    plan: str = "month"


@router.get("/plans")
async def plans():
    """Публичный список планов (звёзды, дни, названия)."""
    return {"plans": [
        {"id": pid, "stars": stars, "days": days, "title": title}
        for pid, (stars, days, title) in billing.PLANS.items()
    ]}


@router.get("/status")
async def status(user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        return await billing.status_dto(s, int(user.id))


@router.post("/checkout")
async def checkout(body: CheckoutIn, user: CurrentUser = Depends(tg_user)):
    """Ссылка на счёт Telegram-звёздами → открывается WebApp.openInvoice.
    Оплата подтверждается в polling-боте (successful_payment)."""
    if is_guest_id(int(user.id)):
        raise HTTPException(400, "оплата звёздами доступна в Telegram: открой "
                                 "мини-апп из бота и войди по коду")
    if not settings.BOT_TOKEN:
        raise HTTPException(500, "платежи не настроены (BOT_TOKEN)")
    link = await billing.link_invoice(
        settings.BOT_TOKEN, body.plan, int(user.id),
        proxy=settings.PROXY_URL or None)
    if not link:
        raise HTTPException(502, "не удалось создать счёт — попробуй позже")
    return {"invoice_link": link, "plan": body.plan}
