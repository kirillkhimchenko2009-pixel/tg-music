"""Доставка (services/pipeline): новые DOWNLOAD_ALLOWED-источники качаются
по официальным ссылкам; per-track разрешение уважается (gated/без ссылки —
не качаем и не переводим на yt-dlp)."""
from __future__ import annotations

import pytest

from services import pipeline
from services.providers.base import TrackMeta


def _meta(source: str, **kw) -> TrackMeta:
    base = dict(source=source, external_id="123", title="Трек", artist="Артист",
                downloadable=True)
    base.update(kw)
    return TrackMeta(**base)


@pytest.fixture
def capture(monkeypatch):
    """Перехват downloader.download_direct: возвращает фейковый результат."""
    calls: list[dict] = []

    async def fake_direct(url, meta, q, on_progress=None, headers=None,
                          use_proxy=True):
        calls.append({"url": url, "source": meta.source, "quality": q,
                      "headers": headers, "use_proxy": use_proxy})
        return "downloaded"

    monkeypatch.setattr(pipeline.downloader, "download_direct", fake_direct)
    return calls


class TestPromodjDelivery:
    async def test_official_download_link_used(self, capture, monkeypatch):
        monkeypatch.setattr(pipeline.pdj, "download_url",
                            lambda m: _async("https://promodj.com/download/123/x.mp3"))
        res = await pipeline._download_inner(_meta("promodj"), "aac320", "", None)
        assert res == "downloaded"
        c = capture[0]
        assert c["url"].startswith("https://promodj.com/download/")
        assert c["headers"]["Referer"] == "https://promodj.com/"

    async def test_no_link_no_fallback_to_ytdlp(self, capture, monkeypatch):
        """Без официальной ссылки — None (id не yt-совместим), а не yt-dlp."""
        monkeypatch.setattr(pipeline.pdj, "download_url", lambda m: _async(None))
        assert await pipeline._download_inner(_meta("promodj"), "aac320", "", None) is None
        assert not capture

    async def test_downloadable_false_respected(self, capture, monkeypatch):
        """downloadable=False → провайдер вернёт None → не качаем."""
        async def reject(m):
            assert m.downloadable is False
            return None
        monkeypatch.setattr(pipeline.pdj, "download_url", reject)
        assert await pipeline._download_inner(
            _meta("promodj", downloadable=False), "aac320", "", None) is None
        assert not capture


class TestAudiusDelivery:
    async def test_download_endpoint_when_allowed(self, capture, monkeypatch):
        monkeypatch.setattr(pipeline.aud, "download_url",
                            lambda m: _async("https://cdn.audius/x.mp3"))
        res = await pipeline._download_inner(_meta("audius"), "aac320", "", None)
        assert res == "downloaded"
        assert capture[0]["url"] == "https://cdn.audius/x.mp3"

    async def test_gated_uses_public_stream(self, capture, monkeypatch):
        """Платное скачивание не обходим, но официальный публичный стрим валиден."""
        async def no_download(m):
            assert m.downloadable is False          # gated → downloadable=False
            return None

        monkeypatch.setattr(pipeline.aud, "download_url", no_download)
        monkeypatch.setattr(pipeline.aud, "stream_url",
                            lambda m: _async("https://cdn.audius/stream.mp3"))
        m = _meta("audius", downloadable=False)
        assert await pipeline._download_inner(m, "aac320", "", None) == "downloaded"
        assert capture[0]["url"] == "https://cdn.audius/stream.mp3"

    async def test_nothing_available_returns_none(self, capture, monkeypatch):
        async def none_url(m):
            return None
        monkeypatch.setattr(pipeline.aud, "download_url", none_url)
        monkeypatch.setattr(pipeline.aud, "stream_url", none_url)
        assert await pipeline._download_inner(_meta("audius"), "aac320", "", None) is None
        assert not capture


class TestCcmixterDelivery:
    async def test_cc_download_used(self, capture, monkeypatch):
        monkeypatch.setattr(pipeline.ccm, "download_url",
                            lambda m: _async("https://ccmixter.org/content/x.mp3"))
        res = await pipeline._download_inner(_meta("ccmixter"), "aac320", "", None)
        assert res == "downloaded"
        c = capture[0]
        assert c["headers"]["Referer"] == "https://ccmixter.org/"
        assert c["use_proxy"] is False


def _async(value):
    async def _f(*a, **kw):
        return value
    return _f()


class TestCandidateChain:
    """_candidates: свои источники попадают в цепочку первыми."""

    async def test_new_sources_are_own_candidates(self, db_session):
        from services.ingest import _candidates
        for src in ("promodj", "audius", "ccmixter"):
            chain = await _candidates(db_session, None, _meta(src), "aac320")
            assert any(m.source == src for m, _ in chain), src
