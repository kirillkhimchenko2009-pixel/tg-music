"""Аутентификация Mini App: подпись Telegram initData (внутри Telegram) и
Bearer-токен (для обычного браузера; сессия выдаётся тихо через /api/auth/guest).

Важно (2025+): Telegram добавил поле `signature`, и теперь имеет смысл
уходить в его включение в data-check-string — проверено на живом initData;
hash по-прежнему исключён (см. validate_init_data).
Реальные клиенты кодируют значения по-разному (плюс/процент),
поэтому пробуем несколько интерпретаций значений."""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, unquote_plus

from fastapi import Header, HTTPException

from core.config import settings

log = logging.getLogger("server.auth")
SECRET = b"WebAppData"
_BEARER_KEY = hashlib.sha256((settings.BOT_TOKEN + "|vibe-bearer-v1").encode()).digest()
_BEARER_DAYS = 30
_DIAG = Path(__file__).resolve().parent.parent / "data" / "diag_initdata.txt"

# Гостевые id (браузер без Telegram) лежат далеко за пределами реальных
# Telegram id (~10^10). В такой чат через Bot API отправить ничего нельзя.
GUEST_ID_BASE = 1_000_000_000_000


def is_guest_id(user_id: int) -> bool:
    return int(user_id) >= GUEST_ID_BASE


def make_bearer(user_id: int, days: int = _BEARER_DAYS) -> str:
    """Токен 'uid.exp.sig' — привязан к пользователю, живёт days дней."""
    exp = int(time.time()) + days * 86400
    msg = f"{user_id}:{exp}".encode()
    sig = hmac.new(_BEARER_KEY, msg, hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{exp}.{sig}"


def verify_bearer(token: str) -> int | None:
    """Возвращает user_id или None (просрочен/поддельный)."""
    try:
        uid_s, exp_s, sig = token.strip().split(".")
        exp = int(exp_s)
        if exp < time.time():
            return None
        msg = f"{uid_s}:{exp}".encode()
        calc = hmac.new(_BEARER_KEY, msg, hashlib.sha256).hexdigest()[:32]
        return int(uid_s) if hmac.compare_digest(calc, sig) else None
    except Exception:
        return None


def _pairs_variants(init_data: str) -> list[dict[str, str]]:
    """Один initData в трёх интерпретациях значений (без hash/signature)."""
    segs = [s for s in init_data.split("&") if "=" in s]
    raw = {s.split("=", 1)[0]: s.split("=", 1)[1] for s in segs}
    keep = [k for k in raw if k not in ("hash", "signature")]
    variants: list[dict[str, str]] = []

    def build(conv):
        d = {}
        for k in keep:
            d[k] = conv(raw[k])
        return {k: d[k] for k in sorted(d)}

    variants.append(build(unquote_plus))          # parse_qsl-стиль
    variants.append(build(unquote))               # '+' сохранён
    variants.append(build(lambda v: v))           # как есть
    return variants


def _display_init_data(user: dict) -> dict:
    """Человекочитаемое имя из user json (для userbox)."""
    out = CurrentUser(user)
    return out


def validate_init_data(init_data: str) -> dict:
    """Проверяет подпись initData и возвращает dict пользователя.
    Проверено на живых данных: Telegram считает hash по СЫРЫМ значениям,
    поэтому основной вариант — без декодирования; json user разбираем
    отдельно с URL-декодированием."""
    if not init_data:
        raise HTTPException(401, "нет initData")
    raw_map: dict[str, str] = {}
    for s in init_data.split("&"):
        if "=" in s:
            k, v = s.split("=", 1)
            raw_map[k] = v
    given_hash = raw_map.get("hash", "")
    if not given_hash:
        raise HTTPException(401, "нет hash")
    keep = [k for k in raw_map if k != "hash"]   # signature участвует с 2025+
    secret_key = hmac.new(SECRET, settings.BOT_TOKEN.encode(), hashlib.sha256).digest()

    def check(values: dict[str, str], tag: str) -> dict | None:
        dc = "\n".join(f"{k}={values[k]}" for k in sorted(values) if k in values)
        calc = hmac.new(secret_key, dc.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(calc, given_hash):
            log.info("initData matched (%s)", tag)
            return values
        return None

    def _with_sig(conv):
        return {k: conv(raw_map[k]) for k in keep}

    def _sans_sig(conv):
        return {k: conv(raw_map[k]) for k in keep
                if k != "signature"}

    variants: list[tuple[str, dict[str, str]]] = [
        ("incl-sig raw", _with_sig(lambda v: v)),
        ("incl-sig pct", _with_sig(unquote)),      # 2025+: подтверждено на живом клиенте
        ("incl-sig plus", _with_sig(unquote_plus)),
        ("legacy raw", _sans_sig(lambda v: v)),
        ("legacy pct", _sans_sig(unquote)),
        ("legacy plus", _sans_sig(unquote_plus)),
    ]
    for tag, values in variants:
        if check(values, tag) is not None:
            u = raw_map.get("user", "")
            try:
                user = json.loads(unquote(u))
            except Exception:
                user = {}
            if not user.get("id"):
                try:
                    user = json.loads(unquote_plus(u))
                except Exception:
                    user = {}
            if not user.get("id"):
                raise HTTPException(401, "нет user в initData")
            return user
    # диагностика: срез реального initData (свои данные) для анализа
    try:
        stamp = datetime.now(timezone.utc).strftime("%m-%d %H:%M:%S")
        with open(_DIAG, "a", encoding="utf-8") as f:
            f.write(f"--- {stamp} FAIL keys={sorted(keep)}\n")
            f.write(init_data + "\n")
    except Exception:
        pass
    log.warning("initData signature mismatch; fields=%s", ",".join(sorted(keep)))
    raise HTTPException(401, "неверная подпись initData")


class CurrentUser(dict):
    @property
    def id(self) -> int:
        return int(self["id"])

    @property
    def username(self):
        return self.get("username")


def tg_user(init_data: str = Header(alias="X-Init-Data", default=""),
            authorization: str = Header(default=""),
            x_user_id: str = Header(default="")) -> CurrentUser:
    """Возвращает пользователя: initData (внутри Telegram) → Bearer токен
    (обычный браузер после /code) → DEV_MODE по id (локальный тест).

    initData проверяем ПЕРВЫМ: в localStorage мог остаться гостевой Bearer
    с прошлого визита в браузере, и он не должен перекрывать настоящий
    Telegram-профиль при открытии мини-аппа из чата."""
    if init_data:
        return CurrentUser(validate_init_data(init_data))
    if authorization.lower().startswith("bearer "):
        uid = verify_bearer(authorization[7:])
        if uid:
            return CurrentUser(id=uid, username=None, first_name="Browser")
    if settings.DEV_MODE and x_user_id and not init_data:
        return CurrentUser(id=int(x_user_id), username="dev", first_name="DEV")
    return CurrentUser(validate_init_data(init_data))
