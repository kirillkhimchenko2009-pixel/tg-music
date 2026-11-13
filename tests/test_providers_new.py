"""7 новых адаптеров (мастер-промпт §8): 10 сценариев на каждый.

Сценарии: доступность, поиск кириллицей, поиск латиницей, метаданные,
официальная ссылка, обложка, разрешение скачивания (per-track), запрет
загрузки, отсутствие API, ошибка сети. Сеть замокана httpx.MockTransport —
живые пробы площадок делаются отдельно (конвенция проекта).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from services import registry
from services.providers import (audius, ccmixter, fma, lisen, mts, promodj,
                                vk)
from services.providers.base import TrackMeta

# ------------------------------------------------------------------ фейковые ответы

PROMODJ_HTML_1 = (
    '<div class="track2 track2_no_avatar player_standard">'
    '<div class="title"><a amba="file:7201579" href='
    '"https://promodj.com/costya/podcasts/7201579/Kino_Mesto_dlya_shaga"'
    ' class="invert" style="margin-right:1px">Кино ( Место для шага вперёд ) #1</a></div>'
    '<span class="player_standard_tool player_standard_tool__time">39:04</span>'
    '<a class="player_standard_tool player_standard_tool__downloads" href='
    '"https://promodj.com/download/7201579/%D0%9A%D0%B8%D0%BD%D0%BE.mp3" title="Download">1 625</a>'
    '<a class="player_standard_tool player_standard_tool__bitrate " href='
    '"https://promodj.com/source/7201579/x.mp3">320</a>'
    '</div>')

PROMODJ_HTML_2 = (  # href ДО class="invert", без ссылки скачивания
    '<div class="track2 player_standard">'
    '<a href="https://promodj.com/deejayantonio/remixes/7295851/KINO_Pechal_Dj_Antonio_Remix" '
    'class="invert">КИНО - Печаль (Dj Antonio Remix)</a>'
    '<span class="player_standard_tool player_standard_tool__time">2:58</span>'
    '</div>')

PROMODJ_HTML_3 = (  # вариант с аватаром (обложкой)
    '<div class="player_mini player_mini__classic">'
    '<a href="https://promodj.com/havaloff/mixes/7956540/Ambient_mix" '
    'class="player_mini_avatar" style="background-image:url(https://cdn.promodj.com/afs/abc123)"></a>'
    '<div class="player_mini_title"><a href='
    '"https://promodj.com/havaloff/mixes/7956540/Ambient_mix" class="invert">'
    'Ambient — это прекрасно</a></div>'
    '<span class="player_standard_tool player_standard_tool__time">7:31</span>'
    '</div>')


def _promodj_search_payload() -> dict:
    return {"total": 3, "perpage": 10, "results": [
        {"kind": "user", "kindID": "2373695", "html": "<div>user card</div>"},
        {"kind": "file", "kindID": "7201579", "html": PROMODJ_HTML_1},
        {"kind": "file", "kindID": "7295851", "html": PROMODJ_HTML_2},
        {"kind": "file", "kindID": "7956540", "html": PROMODJ_HTML_3},
    ]}


AUDIUS_TRACKS = {"data": [
    {"id": "7PJmlb7", "title": "КИНО", "duration": 262, "genre": "Electronic",
     "is_downloadable": False, "is_download_gated": False,
     "permalink": "/quok/кино", "release_date": "2022-03-22T14:50:15Z",
     "artwork": {"150x150": "https://cdn.audius/150.jpg",
                 "480x480": "https://cdn.audius/480.jpg"},
     "user": {"name": "Quok", "handle": "quok"}},
    {"id": "NzYQ8", "title": "Gated Track", "duration": 200, "genre": "House",
     "is_downloadable": True, "is_download_gated": True,      # платная покупка
     "permalink": "/x/gated", "release_date": "2024-01-01T00:00:00Z",
     "artwork": {}, "user": {"name": "X", "handle": "x"}},
    {"id": "BAD1", "title": "", "duration": 1, "is_downloadable": False,
     "is_download_gated": False, "permalink": "/x/y", "artwork": {}, "user": {}},
    {"id": "eAPRw", "title": "Free Track", "duration": 180, "genre": "Pop",
     "is_downloadable": True, "is_download_gated": False,      # честное скачивание
     "permalink": "/y/free-track", "release_date": "2021-05-01T00:00:00Z",
     "artwork": {"480x480": "https://cdn.audius/free480.jpg"},
     "user": {"name": "Free Artist", "handle": "freeartist"}},
]}

CCMIXTER_ITEMS = [
    {"upload_id": 71326, "upload_name": "Prosthetic Summer", "user_name": "MalreDeszik",
     "file_page_url": "https://ccmixter.org/files/MalreDeszik/71326",
     "license_url": "https://creativecommons.org/licenses/by/4.0/",
     "license_name": "Attribution (4.0)",
     "files": [
         {"file_id": 130705, "file_name": "MalreDeszik_-_Prosthetic_Summer.mp3",
          "file_nicname": "mp3", "download_url": "https://ccmixter.org/content/MalreDeszik/x.mp3",
          "file_format_info": {"ps": "4:40"}},
         {"file_id": 130706, "file_name": "stems.flac", "file_nicname": "flac",
          "download_url": "https://ccmixter.org/content/MalreDeszik/stems.flac",
          "file_format_info": {"ps": "4:40"}},
     ]},
    {"upload_id": 71321, "upload_name": "No MP3 Item", "user_name": "VJ_Memes",
     "file_page_url": "https://ccmixter.org/files/VJ_Memes/71321",
     "license_url": "https://creativecommons.org/licenses/by-nc/4.0/",
     "license_name": "Attribution-Noncommercial (4.0)",
     "files": [{"file_id": 130700, "file_name": "y.flac", "file_nicname": "flac",
                "download_url": "https://ccmixter.org/content/VJ_Memes/y.flac",
                "file_format_info": {"ps": "3:03"}}]},
    {"upload_id": 71288, "upload_name": "No License Field", "user_name": "DP",
     "files": [{"file_id": 130699, "file_name": "z.mp3", "file_nicname": "mp3",
                "download_url": "https://ccmixter.org/content/DP/z.mp3",
                "file_format_info": {"ps": "8:29"}}]},
]


# ------------------------------------------------------------------ хелперы моков

def _mock(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)


def _promodj_client(with_track_page: bool = True) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "search" in url:
            return httpx.Response(200, json=_promodj_search_payload())
        if with_track_page and "podcasts/7201579" in url:
            return httpx.Response(200, text=PROMODJ_HTML_1)   # страница трека
        return httpx.Response(404, text="no")
    return _mock(handler)


def _audius_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.rstrip("/") == "https://api.audius.co":     # discovery-эндпоинт
            return httpx.Response(200, json={"data": ["https://api.audius.co"]})
        if "/v1/tracks/search" in url:
            assert "app_name=vibe-music" in url                # атрибуция приложения
            return httpx.Response(200, json=AUDIUS_TRACKS)
        if "/v1/tracks/eAPRw/download" in url:
            return httpx.Response(302, headers={"location": "https://cdn.audius/free.mp3"})
        if "/v1/tracks/eAPRw/stream" in url:
            return httpx.Response(200, content=b"ID3audio", headers={"content-type": "audio/mpeg"})
        if "/download" in url:
            return httpx.Response(403, json={"code": 403,
                                             "error": "you are not allowed to download this track"})
        return httpx.Response(404, json={"code": 404, "error": "track not found"})
    return _mock(handler)


def _ccmixter_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if "api/query" in str(request.url):
            return httpx.Response(200, json=CCMIXTER_ITEMS)
        return httpx.Response(404, text="no")
    return _mock(handler)


@pytest.fixture
def promodj_prov():
    prov = promodj.PromoDJProvider()
    prov._client = _promodj_client()
    yield prov


@pytest.fixture
def audius_prov():
    prov = audius.AudiusProvider()
    prov._client = _audius_client()
    prov._host = None
    yield prov


@pytest.fixture
def ccmixter_prov():
    prov = ccmixter.CCMixterProvider()
    prov._client = _ccmixter_client()
    yield prov


# ================================================================== PromoDJ

class TestPromodjProvider:
    async def test_site_available_search_ok(self, promodj_prov):
        got = await promodj_prov.search("кино")
        assert got
        registry.health_mark("promodj", True)
        assert registry.health_all()["promodj"]["ok"] is True

    async def test_search_cyrillic(self, promodj_prov):
        got = await promodj_prov.search("кино", 5)
        assert got, "должен искать кириллицей (официальный поиск)"
        assert any("Кино" in m.title or "КИНО" in m.title for m in got)

    async def test_search_latin(self, promodj_prov):
        got = await promodj_prov.search("zivert", 5)
        assert got  # запрос ушёл и вернулись результаты

    async def test_metadata_fields(self, promodj_prov):
        m = (await promodj_prov.search("кино"))[0]
        assert m.source == "promodj" and m.external_id
        assert m.title and m.artist                  # автор сохраняется
        assert m.duration == 2344                    # 39:04

    async def test_official_link(self, promodj_prov):
        m = (await promodj_prov.search("кино"))[0]
        assert m.page_url and m.page_url.startswith("https://promodj.com/")
        assert registry.get("promodj").supports_official_link

    async def test_cover(self, promodj_prov):
        got = await promodj_prov.search("кино")
        with_cover = [m for m in got if m.cover_url]
        assert with_cover and "cdn.promodj.com" in with_cover[0].cover_url

    async def test_download_permission_per_track(self, promodj_prov):
        """Разрешение — конкретной композиции, не «всему сайту»."""
        got = await promodj_prov.search("кино")
        allowed = [m for m in got if m.downloadable]
        forbidden = [m for m in got if not m.downloadable]
        assert allowed and forbidden                  # per-track: есть оба
        url = await promodj_prov.download_url(allowed[0])
        assert url and url.startswith("https://promodj.com/download/")

    async def test_download_forbidden(self, promodj_prov):
        """Без официальной ссылки скачивания — downloadable=False и None."""
        got = await promodj_prov.search("кино")
        no_dl = next(m for m in got if not m.downloadable)
        assert await promodj_prov.download_url(no_dl) is None

    async def test_no_api_fallback_to_page(self, promodj_prov):
        """Запасной путь разрешения: страница трека → /download/<id>."""
        prov = promodj.PromoDJProvider()
        prov._client = _promodj_client(with_track_page=True)
        m = TrackMeta(source="promodj", external_id="7201579", title="x",
                      page_url="https://promodj.com/costya/podcasts/7201579/Kino_Mesto",
                      downloadable=True)
        assert await prov.download_url(m)

    async def test_network_error_isolated(self):
        prov = promodj.PromoDJProvider()
        prov._client = _mock(lambda r: (_ for _ in ()).throw(httpx.ConnectError("fail")))
        assert await prov.search("кино") == []
        registry.health_mark("promodj", False, error="ConnectError")
        assert registry.health_all()["promodj"]["ok"] is False

    def test_module_level_api(self):
        assert callable(promodj.search) and callable(promodj.download_url)
        assert callable(promodj.search_artists)


# ================================================================== Audius

class TestAudiusProvider:
    async def test_site_available_search_ok(self, audius_prov):
        got = await audius_prov.search("summer")
        assert got
        registry.health_mark("audius", True)

    async def test_search_cyrillic(self, audius_prov):
        got = await audius_prov.search("кино")
        assert any("КИНО" in m.title for m in got)

    async def test_search_latin(self, audius_prov):
        got = await audius_prov.search("summer")
        assert got and got[0].source == "audius"

    async def test_metadata_fields(self, audius_prov):
        m = (await audius_prov.search("summer"))[0]
        assert m.title == "КИНО" and m.artist == "Quok"
        assert m.duration == 262 and m.year == 2022

    async def test_official_link(self, audius_prov):
        m = (await audius_prov.search("summer"))[0]
        assert m.page_url == "https://audius.co/quok/кино"

    async def test_cover(self, audius_prov):
        m = (await audius_prov.search("summer"))[0]
        assert m.cover_url == "https://cdn.audius/480.jpg"   # 480x480 приоритетнее

    async def test_download_permission_per_track(self, audius_prov):
        """is_downloadable и НЕ gated → разрешено конкретному треку."""
        got = await audius_prov.search("summer")
        free = next(m for m in got if m.external_id == "eAPRw")
        assert free.downloadable is True
        url = await audius_prov.download_url(free)
        assert url == "https://cdn.audius/free.mp3"

    async def test_download_forbidden_gated(self, audius_prov):
        """Платная (gated) композиция НЕ считается доступной — не обходим."""
        got = await audius_prov.search("summer")
        gated = next(m for m in got if m.external_id == "NzYQ8")
        assert gated.downloadable is False
        assert await audius_prov.download_url(gated) is None

    async def test_official_api_only_no_drm_bypass(self, audius_prov):
        """Только официальные эндпоинты (download/stream), app_name обязателен."""
        m = TrackMeta(source="audius", external_id="eAPRw", title="x",
                      downloadable=True)
        assert await audius_prov.stream_url(m) is not None

    async def test_network_error_isolated(self):
        prov = audius.AudiusProvider()
        prov._client = _mock(lambda r: (_ for _ in ()).throw(httpx.ConnectError("fail")))
        prov._host = None
        assert await prov.search("summer") == []
        registry.health_mark("audius", False, error="ConnectError")


# ================================================================== ccMixter

class TestCcmixterProvider:
    async def test_site_available_search_ok(self, ccmixter_prov):
        got = await ccmixter_prov.search("summer")
        assert got
        registry.health_mark("ccmixter", True)

    async def test_search_cyrillic(self, ccmixter_prov):
        """Кириллица уходит в официальный API (может вернуть 0 — не падаем)."""
        got = await ccmixter_prov.search("война")
        assert isinstance(got, list)

    async def test_search_latin(self, ccmixter_prov):
        got = await ccmixter_prov.search("summer")
        assert got and got[0].source == "ccmixter"

    async def test_metadata_fields(self, ccmixter_prov):
        m = (await ccmixter_prov.search("summer"))[0]
        assert m.title == "Prosthetic Summer" and m.artist == "MalreDeszik"
        assert m.duration == 280                         # 4:40

    async def test_official_link(self, ccmixter_prov):
        m = (await ccmixter_prov.search("summer"))[0]
        assert m.page_url == "https://ccmixter.org/files/MalreDeszik/71326"

    def test_no_cover_in_api_is_honest(self):
        """Обложек в API нет — supports_cover=False в реестре (честно)."""
        spec = registry.get("ccmixter")
        assert spec.supports_cover is False

    async def test_download_permission_and_license(self, ccmixter_prov):
        """CC-материал: файл отдаёт API, лицензия конкретной композиции."""
        m = (await ccmixter_prov.search("summer"))[0]
        assert m.downloadable is True
        assert m.license == "CC-BY 4.0"                  # из license_url
        url = await ccmixter_prov.download_url(m)
        assert url == "https://ccmixter.org/content/MalreDeszik/x.mp3"

    async def test_download_forbidden_without_file(self, ccmixter_prov):
        prov = ccmixter.CCMixterProvider()
        prov._client = _ccmixter_client()
        m = TrackMeta(source="ccmixter", external_id="71326", title="x",
                      downloadable=False)
        assert await prov.download_url(m) is None

    async def test_license_variants(self, ccmixter_prov):
        got = await ccmixter_prov.search("summer")
        by_nc = next(m for m in got if m.external_id == "71321")
        assert by_nc.license == "CC-BY-NC 4.0"           # NC-вариант различается
        no_lic = next(m for m in got if m.external_id == "71288")
        assert no_lic.license is None                    # нет лицензии — не выдумываем

    async def test_network_error_isolated(self):
        prov = ccmixter.CCMixterProvider()
        prov._client = _mock(lambda r: (_ for _ in ()).throw(httpx.ConnectError("fail")))
        assert await prov.search("summer") == []


# ================================================================== link-cards

LINK_SOURCES = [
    (fma, "fma", "FMA",
     "https://freemusicarchive.org/search?quicksearch="),
    (vk, "vk", "VK Музыка", "https://vk.com/audio?q="),
    (mts, "mts", "МТС Музыка", "https://music.mts.ru/search?q="),
    (lisen, "lisen", "Lisen.to", "https://lisen.to/ru"),
]


@pytest.mark.parametrize("mod,pid,pname,base", LINK_SOURCES)
class TestOfficialLinkOnlyAdapters:
    async def test_site_available_no_fixed_results(self, mod, pid, pname, base):
        """Доступность: честная карточка без фиктивных результатов."""
        got = await mod.search("кино", 5)
        assert len(got) == 1
        assert got[0].source == pid and got[0].downloadable is False
        registry.health_mark(pid, True)

    async def test_search_cyrillic(self, mod, pid, pname, base):
        got = await mod.search("кино")
        assert got and "кино" in got[0].title

    async def test_search_latin(self, mod, pid, pname, base):
        got = await mod.search("kino")
        assert got and "kino" in got[0].external_id

    async def test_no_fictitious_metadata(self, mod, pid, pname, base):
        """Не выдумываем треки: карточка не притворяется музыкой."""
        m = (await mod.search("кино"))[0]
        assert m.duration == 0 and not m.downloadable
        assert pname in m.title

    async def test_official_link(self, mod, pid, pname, base):
        m = (await mod.search("кино"))[0]
        assert m.page_url and m.page_url.startswith(base)
        spec = registry.get(pid)
        assert spec.supports_official_link is True

    def test_no_cover_without_api(self, mod, pid, pname, base):
        spec = registry.get(pid)
        assert spec.supports_cover is False              # честно: обложек нет

    async def test_download_permission_never_site_wide(self, mod, pid, pname, base):
        """DOWNLOAD_ALLOWED не присваивается сайту целиком."""
        assert registry.mode_of(pid) == registry.ProviderMode.OFFICIAL_LINK_ONLY
        m = (await mod.search("кино"))[0]
        assert m.downloadable is False

    async def test_download_forbidden_no_url(self, mod, pid, pname, base):
        m = (await mod.search("кино"))[0]
        assert m.page_url                                 # только официальная ссылка

    def test_no_api_mode(self, mod, pid, pname, base):
        spec = registry.get(pid)
        assert spec.api_status in ("none", "html_only")
        assert spec.supports_metadata is False            # API нет — не заявляем

    async def test_network_error_no_crash(self, mod, pid, pname, base):
        """Ошибки сети не всплывают: у карточек нет сетевых вызовов вовсе."""
        got = await mod.search("  ")                      # пустой запрос
        assert got == []

    def test_module_level_api(self, mod, pid, pname, base):
        assert callable(mod.search)
