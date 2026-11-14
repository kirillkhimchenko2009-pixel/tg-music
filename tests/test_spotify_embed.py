# -*- coding: utf-8 -*-
"""Спотифай-фолбэк: парсер embed-HTML, сборка TrackMeta, честная ошибка без ключей."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.providers import spotify as sp_mod
from services.providers.spotify import (SpotifyProvider, SpotifyUnavailable,
                                        _entity_from_html, _track_from_embed)

FIXTURE = Path(__file__).parent / "fixtures" / "sp_embed_track.html"


def _fake_track(uri: str, title: str, subtitle: str, ms: int) -> dict:
    return {"uri": uri, "title": title, "subtitle": subtitle, "duration": ms}


def test_entity_from_embed_html_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    ent = _entity_from_html(html)
    assert ent is not None
    assert ent.get("name") == "Prelude for Piano No. 11 in F-Sharp Minor"
    # SSR embed трека не несёт trackList (трек сам entity)
    assert "trackList" not in ent


def test_entity_404_stub_returns_none():
    stub = ('<script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"status":404,"title":"Page not found"}}}</script>')
    assert _entity_from_html(stub) is None


def test_entity_from_resource_base64():
    data = {"data": {"name": "X", "uri": "spotify:track:1", "type": "track"}}
    import base64
    b64 = base64.b64encode(json.dumps(data).encode()).decode()
    html = f'<script data-resource type="application/json">{b64}</script>'
    assert _entity_from_html(html)["name"] == "X"


def test_track_from_embed_meta():
    m = _track_from_embed(_fake_track("spotify:track:abc123", "Song", "Artist A", 200000))
    assert m is not None
    assert m.source == "spotify"
    assert m.external_id == "abc123"
    assert m.title == "Song"
    assert m.artist == "Artist A"
    assert m.duration == 200


def test_track_from_embed_skips_non_track_uri():
    assert _track_from_embed({"uri": "spotify:album:xyz", "title": "t"}) is None


async def test_playlist_embed_collects_tracklist(monkeypatch):
    entity = {"name": "Mix", "trackList": [
        _fake_track("spotify:track:a1", "T1", "A1", 120000),
        _fake_track("spotify:track:a2", "T2", "A2", 90000),
        {"uri": "spotify:local:bad"},          # не трек — пропускается
    ]}
    async def fake_entity(path: str):
        return entity if path.startswith("playlist/") else None
    monkeypatch.setattr(sp_mod, "_embed_entity", fake_entity)

    p = SpotifyProvider(client_id="", secret="")
    assert p.has_credentials is False
    metas = await p._playlist_embed("pid")
    assert [m.external_id for m in metas] == ["a1", "a2"]


async def test_playlist_without_creds_raises_honest_error(monkeypatch):
    async def fake_entity(path: str):
        return None                      # embed недоступен (404-заглушка / нет сети)
    monkeypatch.setattr(sp_mod, "_embed_entity", fake_entity)

    p = SpotifyProvider(client_id="", secret="")
    with pytest.raises(SpotifyUnavailable) as ei:
        await p.playlist("pid")
    assert "SPOTIPY_CLIENT_ID" in str(ei.value)


async def test_playlist_with_creds_falls_back_to_embed(monkeypatch):
    async def fake_entity(path: str):
        return {"name": "Mix", "trackList": [_fake_track("spotify:track:b1", "T", "A", 60000)]}
    monkeypatch.setattr(sp_mod, "_embed_entity", fake_entity)

    p = SpotifyProvider(client_id="cid", secret="sec")
    assert p.has_credentials is True
    # creds есть, но _playlist_creds падает (нет сети) → embed-фолбэк возвращает меты
    monkeypatch.setattr(p, "_playlist_creds",
                        lambda pid: (_ for _ in ()).throw(RuntimeError("network")))
    title, metas = await p.playlist("pid")
    assert metas and metas[0].external_id == "b1"
