# -*- coding: utf-8 -*-
"""_anon_get: fallback-домен при гео-блоке (451) на первичном .net."""
import httpx
import pytest


class _FakeResp:
    def __init__(self, status: int):
        self.status_code = status


class _FakeClient:
    """Вместо httpx.AsyncClient: выдаёт заранее заготовленные ответы по хосту."""
    def __init__(self, statuses: dict):
        self.statuses = statuses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get(self, url: str, **kw):
        for host, st in self.statuses.items():
            if host in url:
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
