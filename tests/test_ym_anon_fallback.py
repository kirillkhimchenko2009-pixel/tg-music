# -*- coding: utf-8 -*-
"""_anon_get: fallback-домен при гео-блоке (451) на первичном .net."""
import httpx
import pytest


class _FakeResp:
    def __init__(self, status: int, body: str = "ok"):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body == "bad":
            raise ValueError("broken body")
        return {"result": {}}


class _FakeClient:
    """Вместо httpx.AsyncClient: выдаёт заранее заготовленные ответы по хосту.

    Значение в statuses: код (int) или (код, тело) — тело "bad" роняет json()."""
    def __init__(self, statuses: dict):
        self.statuses = statuses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get(self, url: str, **kw):
        for host, st in self.statuses.items():
            if host in url:
                if isinstance(st, tuple):
                    return _FakeResp(st[0], st[1])
                return _FakeResp(st)
        raise AssertionError(f"unexpected url {url}")


async def test_anon_get_falls_back_to_ru_on_451(monkeypatch):
    from services.providers import yandex as ym

    client = _FakeClient({"api.music.yandex.net": 451,
                          "api.music.yandex.ru": 200})
    monkeypatch.setattr("services.net.httpx_client",
                        lambda **kw: client)
    r = await ym._anon_get("/users/x/playlists/1")
    assert r.status_code == 200


async def test_anon_get_returns_first_when_both_fail(monkeypatch):
    from services.providers import yandex as ym

    client = _FakeClient({"api.music.yandex.net": 451,
                          "api.music.yandex.ru": 404})
    monkeypatch.setattr("services.net.httpx_client",
                        lambda **kw: client)
    r = await ym._anon_get("/playlist/x")
    assert r.status_code == 451


class _DeadClient:
    """Прокси недоступен: сетевая ошибка при входе в контекст."""
    async def __aenter__(self):
        raise httpx.ConnectError("proxy dead")

    async def __aexit__(self, *a):
        return None

    async def get(self, *a, **kw):
        raise AssertionError("not expected")


async def test_anon_get_uses_ym_proxy(monkeypatch):
    from core.config import settings
    from services.providers import yandex as ym

    captured = {}
    client = _FakeClient({"api.music.yandex.net": 200})

    def fake_httpx_client(**kw):
        captured.update(kw)
        return client

    monkeypatch.setattr("services.net.httpx_client", fake_httpx_client)
    monkeypatch.setattr(settings, "YM_PROXY_URL", "http://ru-proxy:8080")
    r = await ym._anon_get("/users/x/playlists/1")
    assert r.status_code == 200
    assert captured.get("proxy") == "http://ru-proxy:8080"


async def test_anon_get_retries_direct_when_proxy_dead(monkeypatch):
    from core.config import settings
    from services.providers import yandex as ym

    seen = []

    def fake_httpx_client(**kw):
        seen.append(kw.get("proxy"))
        if kw.get("proxy"):
            return _DeadClient()
        return _FakeClient({"api.music.yandex.net": 200})

    monkeypatch.setattr("services.net.httpx_client", fake_httpx_client)
    monkeypatch.setattr(settings, "YM_PROXY_URL", "http://dead-proxy:1")
    r = await ym._anon_get("/users/x/playlists/1")
    assert r.status_code == 200
    # прокси пробуют 4 раза (длинная+короткая ×2), потом напрямую
    assert seen == ["http://dead-proxy:1"] * 4 + [None]


async def test_anon_get_corrupt_body_falls_to_mirror(monkeypatch):
    """200 с битым телом через прокси → пробуем зеркало, а не тащим мусор наверх."""
    from core.config import settings
    from services.providers import yandex as ym

    client = _FakeClient({"api.music.yandex.net": (200, "bad"),
                          "api.music.yandex.ru": 200})
    monkeypatch.setattr("services.net.httpx_client", lambda **kw: client)
    monkeypatch.setattr(settings, "YM_PROXY_URL", "http://ru-proxy:8080")
    r = await ym._anon_get("/users/x/playlists/1")
    assert r.status_code == 200


async def test_anon_get_double_corrupt_200_is_honest_error(monkeypatch):
    """Оба 200, но оба тела битые → честная RuntimeError, не падение на json()."""
    from core.config import settings
    from services.providers import yandex as ym

    client = _FakeClient({"api.music.yandex.net": (200, "bad"),
                          "api.music.yandex.ru": (200, "bad")})
    monkeypatch.setattr("services.net.httpx_client", lambda **kw: client)
    monkeypatch.setattr(settings, "YM_PROXY_URL", "http://ru-proxy:8080")
    with pytest.raises(RuntimeError):
        await ym._anon_get("/users/x/playlists/1")
