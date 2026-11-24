"""Биллинг мини-аппа: тиры/сроки, статус подписки, чекаут за звёзды."""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

from bot import runtime
from core.config import settings
from core.models import User, utcnow
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


@router.get("/status")
async def status(user: CurrentUser = Depends(tg_user)):
    async with runtime.get_session() as s:
        dto = await billing.status_dto(s, int(user.id))
        me = (await s.execute(select(User).where(
            User.id == int(user.id)))).scalar_one_or_none()
        dto["admin"] = bool(me is not None and me.is_admin)
        return dto


@router.get("/admin/stats")
async def admin_stats(user: CurrentUser = Depends(tg_user)):
    """Выручка и активации: только is_admin из БД."""
    from core.models import PromoCode, PromoRedemption, StarPayment, Subscription
    async with runtime.get_session() as s:
        me = (await s.execute(select(User).where(
            User.id == int(user.id)))).scalar_one_or_none()
        if me is None or not me.is_admin:
            raise HTTPException(403, "только для админа")

        stars_total = (await s.execute(select(func.coalesce(func.sum(StarPayment.amount), 0)))).scalar()
        pays_total = (await s.execute(select(func.count(StarPayment.id)))).scalar()
        last = (await s.execute(
            select(StarPayment).order_by(StarPayment.created_at.desc()).limit(30))).scalars().all()
        subs = (await s.execute(select(Subscription))).scalars().all()

        import time as _t
        cutoff = utcnow() - timedelta(days=14)
        daily_rows = (await s.execute(
            select(StarPayment.created_at, StarPayment.amount, StarPayment.tier)
            .where(StarPayment.created_at >= cutoff))).all()
        daily = {}
        for at, amount, tier in daily_rows:
            key = at.date().isoformat()
            daily.setdefault(key, {"stars": 0, "count": 0})
            daily[key]["stars"] += int(amount or 0)
            daily[key]["count"] += 1

        redemptions = (await s.execute(
            select(PromoRedemption).order_by(PromoRedemption.at.desc()).limit(30))).scalars().all()
        promos = (await s.execute(select(PromoCode))).scalars().all()

        return {
            "revenue_stars": int(stars_total or 0),
            "payments": int(pays_total or 0),
            "subs_active": {
                "pro": sum(1 for x in subs if x.tier == "pro" and billing.is_pro(x)),
                "lite": sum(1 for x in subs if x.tier == "lite" and x.status == "active"
                            and x.expires_at and x.expires_at > utcnow()),
                "lifetime": sum(1 for x in subs if x.expires_at is None and x.status == "active"),
            },
            "daily": {k: daily[k] for k in sorted(daily)},
            "last_payments": [{
                "user_id": p.user_id, "tier": p.tier, "plan": p.plan,
                "stars": p.amount, "charge_id": p.charge_id[:24],
                "at": p.created_at.isoformat() if p.created_at else None,
            } for p in last],
            "promos": [{
                "code": c.code, "tier": c.tier, "days": c.days,
                "used": c.used, "max_uses": c.max_uses, "active": c.active,
            } for c in promos],
            "redemptions": [{
                "code": r.code, "user_id": r.user_id,
                "at": r.at.isoformat() if r.at else None,
            } for r in redemptions],
        }
