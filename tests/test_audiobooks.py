from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from server.routes import audiobooks


@pytest.fixture
async def client(monkeypatch):
    rows = [
        {"id": "253", "title": "Pride and Prejudice", "authors": [
            {"first_name": "Jane", "last_name": "Austen"}], "language": "English",
            "totaltimesecs": 47204, "url_librivox": "https://librivox.org/pride-and-prejudice-by-jane-austen/",
            "coverart_thumbnail": "https://archive.org/download/book/cover.jpg"},
        {"id": "271", "title": "Dracula", "authors": [
            {"first_name": "Bram", "last_name": "Stoker"}], "language": "English",
            "totaltimesecs": 0, "url_librivox": "https://librivox.org/dracula/"},
    ]

    async def fetch(params):
        if params.get("id"):
            raise httpx.HTTPStatusError("should use details mock", request=None, response=None)
        if params.get("title"):
            return [rows[0]]
        if params.get("author"):
            return [rows[0]]
        return rows

    monkeypatch.setattr(audiobooks, "_fetch_page", fetch)
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


async def test_catalog_is_paginated_and_includes_large_collection_hint(client):
    response = await client.get("/api/audiobooks/catalog?limit=2&offset=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["offset"] == 30
    assert payload["total_hint"] == "20 000+"
    assert payload["books"][0]["author"] == "Jane Austen"
    assert payload["books"][0]["source"].startswith("LibriVox")


async def test_search_merges_title_and_author_results_without_duplicates(client):
    response = await client.get("/api/audiobooks/catalog?q=austen")
    assert response.status_code == 200
    assert [book["id"] for book in response.json()["books"]] == ["253"]


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


def test_archive_host_allowlist_rejects_external_or_insecure_urls():
    assert audiobooks._trusted_archive_url("https://www.archive.org/download/book/01.mp3")
    assert audiobooks._trusted_archive_url("https://ia800201.us.archive.org/download/book/01.mp3")
    assert not audiobooks._trusted_archive_url("http://archive.org/download/book/01.mp3")
    assert not audiobooks._trusted_archive_url("https://archive.org.example.com/download/book/01.mp3")
    assert not audiobooks._trusted_archive_url("https://evil.example/audio.mp3")
