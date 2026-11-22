from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

import httpx
import pytest
from fastapi import FastAPI

from server.routes import podcasts


ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def _rss(audio_url="https://cdn.radio-t.com/episode.mp3", guid="episode-guid"):
    return f'''<?xml version="1.0"?>
    <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
      <title>Test show</title><itunes:image href="https://cdn.radio-t.com/cover.jpg"/>
      <item><guid>{guid}</guid><title>Тестовый выпуск</title>
        <description>Технологии и разговоры</description><pubDate>Sat, 21 Nov 2026 17:07:48 +0000</pubDate>
        <itunes:duration>1:02:03</itunes:duration>
        <enclosure url="{audio_url}" length="123456" type="audio/mpeg"/>
      </item>
    </channel></rss>'''.encode()


class TestPodcastRSS:
    def test_parses_podcast_episode_and_keeps_audio_source_private(self):
        show = podcasts._parse_feed("radio-t", _rss())
        assert show["title"] == "Радио-Т"
        episode = show["episodes"][0]
        assert episode["title"] == "Тестовый выпуск"
        assert episode["duration"] == 3723
        assert episode["size"] == 123456
        assert episode["audio_url"] == f"/api/podcasts/audio/radio-t/{episode['id']}"
        assert episode["_source_url"] == "https://cdn.radio-t.com/episode.mp3"

    def test_rejects_non_allowlisted_and_non_https_enclosures(self):
        assert podcasts._parse_feed("radio-t", _rss("https://evil.example/episode.mp3"))["episodes"] == []
        assert podcasts._parse_feed("radio-t", _rss("http://cdn.radio-t.com/episode.mp3"))["episodes"] == []

    def test_rejects_dtd_and_oversized_feed(self):
        with pytest.raises(ValueError):
            podcasts._parse_feed("radio-t", b"<!DOCTYPE rss><rss/>")
        with pytest.raises(ValueError):
            podcasts._parse_feed("radio-t", b" " * (podcasts._MAX_FEED_BYTES + 1))

    def test_episode_id_is_stable_hash_of_feed_guid(self):
        show = podcasts._parse_feed("radio-t", _rss(guid="stable-guid"))
        expected = hashlib.sha256(b"stable-guid").hexdigest()[:20]
        assert show["episodes"][0]["id"] == expected


@pytest.fixture
async def client(monkeypatch):
    async def feed(show_id):
        show = podcasts._parse_feed(show_id, _rss() if show_id == "radio-t" else b"<rss><channel><title>empty</title></channel></rss>")
        show["id"] = show_id
        show["title"] = podcasts.FEEDS[show_id]["title"]
        show["category"] = podcasts.FEEDS[show_id]["category"]
        show["description"] = podcasts.FEEDS[show_id]["description"]
        show["color"] = podcasts.FEEDS[show_id]["color"]
        if show["episodes"]:
            show["episodes"][0]["show_id"] = show_id
            show["episodes"][0]["show_title"] = show["title"]
            show["episodes"][0]["audio_url"] = f"/api/podcasts/audio/{show_id}/{show['episodes'][0]['id']}"
        return show

    async def get_catalog():
        return [await feed(show_id) for show_id in podcasts.FEEDS]

    monkeypatch.setattr(podcasts, "_get_catalog", get_catalog)
    app = FastAPI()
    app.include_router(podcasts.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_catalog_does_not_expose_upstream_enclosure_urls(client):
    response = await client.get("/api/podcasts/catalog")
    assert response.status_code == 200
    data = response.json()
    assert len(data["shows"]) == 3
    show = next(show for show in data["shows"] if show["id"] == "radio-t")
    episode = show["episodes"][0]
    assert episode["audio_url"].startswith("/api/podcasts/audio/")
    assert "_source_url" not in episode
    assert '"url":"https://cdn.radio-t.com/episode.mp3"' not in response.text


async def test_unknown_show_and_episode_are_rejected(client):
    assert (await client.get("/api/podcasts/audio/unknown/nope")).status_code == 404
    show = podcasts._parse_feed("radio-t", _rss())
    episode_id = show["episodes"][0]["id"]
    assert (await client.get(f"/api/podcasts/audio/radio-t/{episode_id}?download=1")).status_code == 502
