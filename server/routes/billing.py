"""Биллинг мини-аппа: тиры/сроки, статус подписки, чекаут за звёзды."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from bot import runtime
from core.config import settings
from core.models import User
from server.auth import CurrentUser, is_guest_id, tg_user
from services import billing
from services.billing import PROMO_SEED, create_promo, redeem, seed_promos

LOG = logging.getLogger("routes.billing")
router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutIn(BaseModel):
    plan: str = "pro-month"        # "<tier>-<term>", напр. lite-month / pro-year


@router.get("/plans")
async def plans():
    """Публичный каталог: два тира, у каждого три срока."""
    return {"tiers": [
        {"id": tid, "title": t["name"], "perks": t["perks"],
         "terms": [{"id": term, "days": billing.PLAN_DAYS[term], "stars": t["stars"][term],
                    "title": billing.TERM_TITLES[term]}
                   for term in ("month", "half", "year")]}
        for tid, t in billing.TIERS.items()
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
    tier, term = billing.parse_plan(body.plan)
    if not settings.BOT_TOKEN:
        raise HTTPException(500, "платежи не настроены (BOT_TOKEN)")
    link = await billing.link_invoice(
        settings.BOT_TOKEN, tier, term, int(user.id),
        proxy=settings.PROXY_URL or None)
    if not link:
        raise HTTPException(502, "не удалось создать счёт — попробуй позже")
    return {"invoice_link": link, "tier": tier, "term": term}


class RedeemIn(BaseModel):
    code: str


@router.post("/promo/redeem")
async def promo_redeem(body: RedeemIn, user: CurrentUser = Depends(tg_user)):
    """Активировать промокод (подписка на аккаунт, гостям тоже можно)."""
    async with runtime.get_session() as s:
        await seed_promos(s)                 # сид-коды (sever1908) — при первом обращении
        try:
            res = await redeem(s, int(user.id), body.code)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return res


class PromoIn(BaseModel):
    code: str
    tier: str = "pro"
    days: int | None = None          # null = навсегда
    max_uses: int | None = None      # null = безлимит
    note: str = ""


@router.post("/admin/promo")
async def admin_promo(body: PromoIn, user: CurrentUser = Depends(tg_user)):
    """Создать промокод (только админ по БД-флагу is_admin)."""
    async with runtime.get_session() as s:
        me = (await s.execute(select(User).where(
            User.id == int(user.id)))).scalar_one_or_none()
        if me is None or not me.is_admin:
            raise HTTPException(403, "только для админа")
        try:
            res = await create_promo(s, body.code, body.tier, body.days,
                                     body.max_uses, body.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return res
