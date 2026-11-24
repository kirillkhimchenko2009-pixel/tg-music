"""Биллинг Vibe: два тира подписки за Telegram-звёзды (currency XTR).

Lite — базовые бонусы, Pro — всё сразу. У каждого тира три срока
(месяц / полгода / год). Оплата через createInvoiceLink →
WebApp.openInvoice; подтверждение прилетает в polling-бот
(bot/handlers/billing.py), который зовёт grant().
"""
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import StarPayment, Subscription, utcnow

LOG = logging.getLogger("services.billing")

# срок → дней
PLAN_DAYS = {"month": 31, "half": 183, "year": 366}
TERM_TITLES = {"month": "месяц", "half": "полгода", "year": "год"}

# тир → (название, {срок → звёзды}, преимущества)
TIERS: dict[str, dict] = {
    "lite": {
        "name": "Vibe Lite",
        "stars": {"month": 25, "half": 120, "year": 200},
        "perks": ["Безлимитные скачивания треков в чат",
                  "Открытие ссылок и импорт плейлистов без лимитов"],
    },
    "pro": {
        "name": "Vibe Pro",
        "stars": {"month": 50, "half": 250, "year": 450},
        "perks": ["Всё из Lite",
                  "PRO-обложка плеера: аниме-арт локскрина",
                  "Приоритетная волна и очередь скачиваний",
                  "Поддержка развития Vibe"],
    },
}

PAYLOAD_PREFIX = "vibe-sub"


def parse_plan(s: str) -> tuple[str, str]:
    """'pro-year' → ('pro', 'year'); легаси 'year' → ('pro', 'year')."""
    s = str(s or "").strip().lower()
    if s in TIERS or s in PLAN_DAYS:      # легаси-формат одного тира
        return ("pro", s if s in PLAN_DAYS else "year")
    tier, _, term = s.partition("-")
    if tier not in TIERS or term not in PLAN_DAYS:
        return ("pro", "month")
    return (tier, term)


def plan_id(tier: str, term: str) -> str:
    return f"{tier}-{term}"


def is_pro(sub: Subscription | None) -> bool:
    if sub is None or sub.tier != "pro" or sub.status != "active":
        return False
    exp = sub.expires_at
    return exp is not None and exp > utcnow()


def is_lite(sub: Subscription | None) -> bool:
    """Lite считаем активным и когда он сам активен, и когда активен Pro
    (Pro включает всё из Lite)."""
    if sub is None or sub.status != "active":
        return False
    exp = sub.expires_at
    return (sub.tier == "lite" or sub.tier == "pro") and exp is not None and exp > utcnow()


async def active_subscription(s: AsyncSession, user_id: int, tier: str) -> Subscription | None:
    return (await s.execute(select(Subscription).where(
        Subscription.user_id == int(user_id),
        Subscription.tier == tier))).scalar_one_or_none()


async def status_dto(s: AsyncSession, user_id: int) -> dict:
    def dto(sub: Subscription | None, tier: str) -> dict | None:
        ok = (is_pro(sub) if tier == "pro" else is_lite(sub)) and sub is not None \
            and sub.tier == tier
        return {"active": ok, "plan": sub.plan if ok else None,
                "expires_at": sub.expires_at.isoformat() if (ok and sub.expires_at) else None}

    pro_sub = await active_subscription(s, int(user_id), "pro")
    lite_sub = await active_subscription(s, int(user_id), "lite")
    pro = is_pro(pro_sub)
    lite = is_lite(lite_sub) or pro        # pro включает lite
    return {
        "pro": pro, "lite": lite,
        "best": "pro" if pro else ("lite" if lite else None),
        "tiers": {"pro": dto(pro_sub, "pro"), "lite": dto(lite_sub, "lite")},
    }


async def grant(s: AsyncSession, user_id: int, tier: str, term: str,
                charge_id: str, amount: int, payload: str = "") -> dict:
    """Зачислить подписку тира tier на срок term. Идемпотентно по charge_id
    (Telegram может ретраить successful_payment)."""
    tier = tier if tier in TIERS else "pro"
    term = term if term in PLAN_DAYS else "month"
    dup = (await s.execute(select(StarPayment).where(
        StarPayment.charge_id == charge_id))).scalar_one_or_none()
    if dup is not None:
        LOG.info("billing: повтор charge_id=%s — пропускаю", charge_id)
        sub = await active_subscription(s, int(user_id), tier)
        return {"granted": False, "expires_at": sub.expires_at.isoformat() if sub else None}

    now = utcnow()
    sub = await active_subscription(s, int(user_id), tier)
    if sub is None:
        sub = Subscription(user_id=int(user_id), tier=tier, plan=term,
                           started_at=now, expires_at=now)
        s.add(sub)
    base = max(now, sub.expires_at) if sub.expires_at else now
    sub.tier = tier
    sub.plan = term
    sub.status = "active"
    sub.expires_at = base + timedelta(days=PLAN_DAYS[term])
    s.add(StarPayment(user_id=int(user_id), tier=tier, plan=term,
                      amount=int(amount), charge_id=str(charge_id),
                      payload=str(payload or "")))
    await s.commit()
    LOG.info("billing: %s «%s» (%s) до %s (uid=%s, %s★)",
             "продлена" if base > now else "выдана", TIERS[tier]["name"],
             term, sub.expires_at.isoformat(), user_id, amount)
    return {"granted": True, "tier": tier, "term": term,
            "expires_at": sub.expires_at.isoformat()}


async def link_invoice(bot_token: str, tier: str, term: str, user_id: int,
                       proxy: str | None = None) -> str | None:
    """createInvoiceLink → https://t.me/$... ссылка для WebApp.openInvoice."""
    if tier not in TIERS or term not in PLAN_DAYS:
        return None
    stars = TIERS[tier]["stars"][term]
    import httpx
    url = f"https://api.telegram.org/bot{bot_token}/createInvoiceLink"
    body = {
        "title": f"{TIERS[tier]['name']} · {TERM_TITLES[term]}",
        "description": "Подписка Vibe: " + "; ".join(TIERS[tier]["perks"]),
        "payload": f"{PAYLOAD_PREFIX}:{tier}-{term}:{int(user_id)}",
        "currency": "XTR",
        "prices": [{"label": f"{TIERS[tier]['name']} · {TERM_TITLES[term]}", "amount": stars}],
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
