"""Keepalive of the Render app from the async Botkeep worker."""
from __future__ import annotations

import asyncio
import logging

import pytest

import run_worker


def test_health_url_uses_service_root():
    assert run_worker._health_url("https://music.example/v2/") == \
        "https://music.example/health"


def test_health_url_requires_absolute_http_url():
    with pytest.raises(ValueError):
        run_worker._health_url("/v2/")


@pytest.mark.asyncio
async def test_keepalive_uses_async_client_and_logs_health(monkeypatch, caplog):
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 30}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def get(self, url):
            calls.append(url)
            return Response()

    async def stop_after_one_ping(_seconds):
        raise asyncio.CancelledError

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(run_worker.asyncio, "sleep", stop_after_one_ping)
    monkeypatch.setenv("WEBAPP_URL", "https://music.example/v2/")

    with caplog.at_level(logging.INFO, logger="keepalive"):
        with pytest.raises(asyncio.CancelledError):
            await run_worker._keepalive()

    assert calls == ["https://music.example/health"]
    assert "keepalive: 200 https://music.example/health" in caplog.text
