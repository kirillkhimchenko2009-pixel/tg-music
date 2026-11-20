"""Вход в браузере по коду из /code: обмен, одноразовость, срок, гость."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

from core.config import settings
from core.db import get_session
from core.models import AuthCode, Base, User, utcnow
from server.auth import make_bearer, tg_user, verify_bearer
from server.routes import auth as auth_route


@pytest.fixture
async def auth_env():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _session():
        async with maker() as s:
            yield s

    app = FastAPI()
    app.include_router(auth_route.router)
    app.dependency_overrides[get_session] = _session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield client, maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clean_attempts():
    auth_route._ATTEMPTS.clear()
    yield
    auth_route._ATTEMPTS.clear()


async def _add_code(maker, code: str = "12345678", uid: int = 555,
                    minutes: int = 10) -> None:
    async with maker() as s:
        s.add(AuthCode(code=code, user_id=uid,
                       expires_at=utcnow() + timedelta(minutes=minutes)))
        await s.commit()


class TestExchange:
    async def test_valid_code_returns_bearer(self, auth_env):
        client, maker = auth_env
        await _add_code(maker)
        r = await client.post("/api/auth/exchange", json={"code": "1234 5678"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["user"]["id"] == 555
        assert verify_bearer(data["token"]) == 555

        async with maker() as s:
            user = await s.get(User, 555)
            assert user is not None
            row = (await s.execute(
                select(AuthCode).where(AuthCode.code == "12345678")
            )).scalar_one()
            assert row.used is True

    async def test_code_is_single_use(self, auth_env):
        client, maker = auth_env
        await _add_code(maker, code="87654321")
        first = await client.post("/api/auth/exchange", json={"code": "87654321"})
        assert first.status_code == 200
        second = await client.post("/api/auth/exchange", json={"code": "87654321"})
        assert second.status_code == 400
        assert "код" in second.json()["detail"]

    async def test_expired_code_rejected(self, auth_env):
        client, maker = auth_env
        await _add_code(maker, code="11112222", minutes=-1)
        r = await client.post("/api/auth/exchange", json={"code": "11112222"})
        assert r.status_code == 400
        assert "истёк" in r.json()["detail"]

    async def test_unknown_and_bad_format(self, auth_env):
        client, _ = auth_env
        r = await client.post("/api/auth/exchange", json={"code": "00000000"})
        assert r.status_code == 400
        r = await client.post("/api/auth/exchange", json={"code": "123"})
        assert r.status_code == 400

    async def test_new_code_invalidates_old(self, auth_env):
        client, maker = auth_env
        await _add_code(maker, code="22223333")
        await _add_code(maker, code="44445555")
        r = await client.post("/api/auth/exchange", json={"code": "22223333"})
        assert r.status_code == 200
        async with maker() as s:
            rows = (await s.execute(
                select(AuthCode).where(AuthCode.code == "44445555")
            )).scalar_one()
            assert rows.used is True


def _signed_init_data(uid: int, auth_date: int | None = None) -> str:
    """Валидный initData: hash считается тем же способом, что и Telegram."""
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAE2e2e",
        "user": json.dumps({"id": uid, "first_name": "TG"},
                           separators=(",", ":")),
    }
    data_check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(),
                      hashlib.sha256).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return "&".join(f"{k}={v}" for k, v in fields.items()) + "&hash=" + digest


class TestInitDataFreshness:
    def test_stale_init_data_is_rejected(self, monkeypatch):
        from fastapi import HTTPException
        from server import auth

        monkeypatch.setattr(auth, "_INIT_DATA_MAX_AGE_SEC", 60)
        stale = _signed_init_data(5339474152, int(time.time()) - 61)
        with pytest.raises(HTTPException, match="устарел"):
            auth.validate_init_data(stale)

    def test_future_init_data_is_rejected(self):
        from fastapi import HTTPException
        from server import auth

        future = _signed_init_data(5339474152, int(time.time()) + 301)
        with pytest.raises(HTTPException, match="будущего"):
            auth.validate_init_data(future)

    def test_invalid_init_data_is_not_logged_verbatim(self, caplog):
        from fastapi import HTTPException
        from server import auth

        payload = _signed_init_data(5339474152) + "&private_marker=do-not-log"
        with pytest.raises(HTTPException):
            auth.validate_init_data(payload)
        assert "private_marker=do-not-log" not in caplog.text
        assert "payload_len=" in caplog.text


class TestAuthPriority:
    def test_init_data_wins_over_guest_bearer(self):
        """В localStorage мог остаться гостевой токен — Telegram-профиль важнее."""
        guest_token = make_bearer(1_000_000_000_777)
        user = tg_user(init_data=_signed_init_data(5339474152),
                       authorization="Bearer " + guest_token)
        assert user.id == 5339474152

    def test_bearer_used_without_init_data(self):
        guest_token = make_bearer(1_000_000_000_777)
        user = tg_user(init_data="", authorization="Bearer " + guest_token)
        assert user.id == 1_000_000_000_777
