"""Биллинг Vibe Pro: подписка за Telegram-звёзды (currency XTR).

Планы, выдача (grant) по successful_payment из Bot API, статус.
Оплата идёт через createInvoiceLink → WebApp.openInvoice; подтверждение
прилетает в polling-бот (bot/handlers/billing.py), который зовёт grant().
"""
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import StarPayment, Subscription, utcnow

LOG = logging.getLogger("services.billing")

# plan → (звёзды, дней, название)
PLANS: dict[str, tuple[int, int, str]] = {
    "month": (50, 31, "месяц"),
    "half": (250, 183, "полгода"),
    "year": (450, 366, "год"),
}

PAYLOAD_PREFIX = "vibe-sub"


def plan_days(plan: str) -> int:
    return PLANS.get(plan, PLANS["month"])[1]


def is_pro(sub: Subscription | None) -> bool:
    if sub is None or sub.status != "active":
        return False
    exp = sub.expires_at
    return exp is not None and exp > utcnow()


async def active_subscription(s: AsyncSession, user_id: int) -> Subscription | None:
    return (await s.execute(select(Subscription).where(
        Subscription.user_id == int(user_id)))).scalar_one_or_none()


async def status_dto(s: AsyncSession, user_id: int) -> dict:
    sub = await active_subscription(s, user_id)
    pro = is_pro(sub)
    return {
        "pro": pro,
        "plan": sub.plan if pro else None,
        "expires_at": sub.expires_at.isoformat() if (pro and sub.expires_at) else None,
    }


async def grant(s: AsyncSession, user_id: int, plan: str,
                charge_id: str, amount: int, payload: str = "") -> dict:
    """Зачислить подписку. Идемпотентно по charge_id (Telegram может
    ретраить successful_payment). Возвращает {'granted', 'expires_at'}."""
    plan = plan if plan in PLANS else "month"
    dup = (await s.execute(select(StarPayment).where(
        StarPayment.charge_id == charge_id))).scalar_one_or_none()
    if dup is not None:
        LOG.info("billing: повтор charge_id=%s — пропускаю", charge_id)
        sub = await active_subscription(s, int(user_id))
        return {"granted": False, "expires_at": sub.expires_at.isoformat() if sub else None}

    now = utcnow()
    sub = await active_subscription(s, int(user_id))
    if sub is None:
        sub = Subscription(user_id=int(user_id), plan=plan,
                           started_at=now, expires_at=now)
        s.add(sub)
    base = max(now, sub.expires_at) if sub.expires_at else now
    sub.plan = plan
    sub.status = "active"
    sub.expires_at = base + timedelta(days=plan_days(plan))
    s.add(StarPayment(user_id=int(user_id), plan=plan, amount=int(amount),
                      charge_id=str(charge_id), payload=str(payload or "")))
    await s.commit()
    LOG.info("billing: %s подписка «%s» до %s (uid=%s, %s★)",
             "продлена" if base > now else "выдана", plan,
             sub.expires_at.isoformat(), user_id, amount)
    return {"granted": True, "expires_at": sub.expires_at.isoformat()}


async def link_invoice(bot_token: str, plan: str, user_id: int,
                       proxy: str | None = None) -> str | None:
    """createInvoiceLink → https://t.me/$... ссылка для WebApp.openInvoice."""
    if plan not in PLANS:
        return None
    stars, _, title = PLANS[plan]
    import httpx
    url = f"https://api.telegram.org/bot{bot_token}/createInvoiceLink"
    body = {
        "title": f"Vibe Pro — {title}",
        "description": "Подписка Vibe Pro: безлимитные скачивания в чат, "
                       "PRO-обложка плеера и приоритет волны.",
        "payload": f"{PAYLOAD_PREFIX}:{plan}:{int(user_id)}",
        "currency": "XTR",
        "prices": [{"label": f"Vibe Pro · {title}", "amount": stars}],
    }
    try:
        async with httpx.AsyncClient(timeout=15, proxy=proxy or None) as cli:
            r = await cli.post(url, json=body)
            data = r.json()
        if data.get("ok"):
            return data["result"]
        LOG.error("billing: createInvoiceLink не удался: %s", data.get("description"))
        return None
    except Exception as e:  # noqa: BLE001
        LOG.error("billing: createInvoiceLink исключение: %r", e)
        return None
