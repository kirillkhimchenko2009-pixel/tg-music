from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from server.routes import audiobooks


@pytest.fixture
async def client(monkeypatch):
    rows = [
        {"identifier": "eugene_onegin_1801_librivox", "title": "Евгений Онегин",
         "creator": "Alexander Pushkin", "language": "rus", "runtime": "4:26:23"},
        {"identifier": "aesops_fables_russian_0905_librivox", "title": "Басни Эзопа",
         "creator": "Aesop", "language": "rus", "runtime": "50:28"},
        {"identifier": "an-english-item", "title": "Pride and Prejudice",
         "creator": "Jane Austen", "language": "eng"},
    ]

    async def archive_search(query, offset, limit, lang=""):
        if "Pushkin" in query:
            matching = [rows[0]]
        elif lang:
            matching = [row for row in rows if row.get("language") == lang]
        else:
            matching = rows
        total = len(matching)
        if lang in ("", "rus") and "Pushkin" not in query:
            total = 76
        return matching[offset:offset + limit], total

    monkeypatch.setattr(audiobooks, "_archive_search", archive_search)
    async def details(book_id):
        return {"id": book_id, "title": "Pride and Prejudice", "author": "Jane Austen",
                "language": "English", "description": "A classic", "duration": 47204,
                "cover_url": "", "cover_large": "", "catalog_url": "https://librivox.org/book/",
                "source": "LibriVox", "chapters": [{"id": "124135", "number": "1",
                    "title": "Chapters 1-3", "language": "English", "duration": 1132,
                    "narrator": "Reader", "audio_url": "/api/audiobooks/audio/253/124135",
                    "_source_url": "https://www.archive.org/download/book/chapter.mp3"}]}
    monkeypatch.setattr(audiobooks, "_upstream_book", details)
    app = FastAPI()
    app.include_router(audiobooks.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_catalog_is_paginated_and_contains_only_russian_recordings(client):
    response = await client.get("/api/audiobooks/catalog?limit=2&offset=0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["offset"] == 0
    assert payload["total_hint"] == "76 русских записей"
    assert [book["id"] for book in payload["books"]] == [
        "eugene_onegin_1801_librivox", "aesops_fables_russian_0905_librivox"
    ]
    assert all(book["language"] == "Русский" for book in payload["books"])
    assert payload["books"][0]["title"] == "Евгений Онегин"
    assert payload["books"][0]["source"].startswith("LibriVox")
    assert payload["has_more"] is True
    assert payload["language"] == "rus"
    assert payload["total"] == 76


async def test_catalog_switches_to_english_recordings(client):
    response = await client.get("/api/audiobooks/catalog?lang=eng")
    assert response.status_code == 200
    payload = response.json()
    assert [book["id"] for book in payload["books"]] == ["an-english-item"]
    assert payload["books"][0]["language"] == "Английский"
    assert payload["language"] == "eng"
    assert payload["total"] == 1


async def test_catalog_all_languages_keeps_every_recording(client):
    response = await client.get("/api/audiobooks/catalog?lang=all")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["books"]) == 3
    assert payload["language"] == "all"


async def test_catalog_rejects_unknown_language(client):
    response = await client.get("/api/audiobooks/catalog?lang=xx")
    assert response.status_code == 400


async def test_search_uses_archive_query_for_russian_title_or_author(client):
    response = await client.get("/api/audiobooks/catalog?q=Pushkin")
    assert response.status_code == 200
    assert [book["id"] for book in response.json()["books"]] == ["eugene_onegin_1801_librivox"]


async def test_short_search_is_not_sent_to_external_catalog(client, monkeypatch):
    async def fail(_params):
        raise AssertionError("short queries should be ignored")
    monkeypatch.setattr(audiobooks, "_fetch_page", fail)
    response = await client.get("/api/audiobooks/catalog?q=a")
    assert response.status_code == 200
    assert response.json()["books"] == []


async def test_book_hides_upstream_audio_url(client):
    response = await client.get("/api/audiobooks/book/253")
    assert response.status_code == 200
    payload = response.json()
    assert payload["chapters"][0]["audio_url"] == "/api/audiobooks/audio/253/124135"
    assert "_source_url" not in response.text
    assert "archive.org/download/book/chapter.mp3" not in response.text


async def test_archive_book_hides_source_url_and_keeps_public_chapter_route(client, monkeypatch):
    async def details(book_id):
        return {"id": book_id, "title": "Евгений Онегин", "chapters": [{
            "id": "1", "title": "Глава 1", "audio_url": f"/api/audiobooks/audio/{book_id}/1",
            "_source_url": f"https://archive.org/download/{book_id}/chapter.mp3",
        }]}

    monkeypatch.setattr(audiobooks, "_archive_book", details)
    response = await client.get("/api/audiobooks/book/eugene_onegin_1801_librivox")
    assert response.status_code == 200
    assert response.json()["chapters"][0]["audio_url"].endswith("/1")
    assert "_source_url" not in response.text
    assert "archive.org/download" not in response.text


def test_archive_chapters_selects_highest_quality_audio_and_builds_proxy_urls():
    chapters = audiobooks._archive_chapters("eugene_onegin_1801_librivox", [
        {"name": "one_64kb.mp3", "source": "original", "format": "64Kbps MP3",
         "title": "Глава первая", "track": "1/2", "length": "61", "size": "100"},
        {"name": "one.mp3", "source": "original", "format": "VBR MP3",
         "title": "Глава первая", "track": "1/2", "length": "62", "size": "200"},
        {"name": "two.mp3", "source": "original", "format": "VBR MP3", "track": "2/2"},
        {"name": "folder/evil.mp3", "source": "original", "format": "VBR MP3"},
        {"name": "cover.jpg", "source": "original", "format": "JPEG"},
    ])
    assert [chapter["title"] for chapter in chapters] == ["Глава первая", "two"]
    assert chapters[0]["_source_url"] == "https://archive.org/download/eugene_onegin_1801_librivox/one.mp3"
    assert chapters[0]["audio_url"] == "/api/audiobooks/audio/eugene_onegin_1801_librivox/1"
    assert chapters[0]["duration"] == 62


def test_archive_host_allowlist_rejects_external_or_insecure_urls():
    assert audiobooks._trusted_archive_url("https://www.archive.org/download/book/01.mp3")
    assert audiobooks._trusted_archive_url("https://ia800201.us.archive.org/download/book/01.mp3")
    assert not audiobooks._trusted_archive_url("http://archive.org/download/book/01.mp3")
    assert not audiobooks._trusted_archive_url("https://archive.org.example.com/download/book/01.mp3")
    assert not audiobooks._trusted_archive_url("https://evil.example/audio.mp3")
