"""Прямые аудио-URL YouTube через InnerTube (как мобильные клиенты).

Проблема: yt-dlp с датацентровых IP (Render) ловит бот-чек YouTube и не может
скачать/разрешить поток. InnerTube-клиенты ANDROID/IOS/VR/TV обычно не ловят
проверку и отдают прямой googlevideo-URL. URL живёт часами, поддерживает Range.
"""
import logging

import httpx

log = logging.getLogger("providers.yt_stream")

PLAYER_URL = "https://www.youtube.com/youtubei/v1/player"
SEARCH_URL = "https://music.youtube.com/youtubei/v1/search"

SEARCH_CTX = {"clientName": "WEB_REMIX", "clientVersion": "1.20240401.01.00",
              "hl": "en", "gl": "US"}
SEARCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CLIENTS = [
    {
        "name": "ANDROID",
        "ctx": {"clientName": "ANDROID", "clientVersion": "19.09.37",
                "androidSdkVersion": 30, "hl": "en", "gl": "US"},
        "ua": "com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip",
    },
    {
        "name": "IOS",
        "ctx": {"clientName": "IOS", "clientVersion": "19.45.4",
                "deviceModel": "iPhone16,2", "osName": "iPhone",
                "osVersion": "18.1.0.22B83", "hl": "en", "gl": "US"},
        "ua": "com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1_0 like Mac OS X;)",
    },
    {
        "name": "ANDROID_VR",
        "ctx": {"clientName": "ANDROID_VR", "clientVersion": "1.60.19",
                "androidSdkVersion": 32, "deviceMake": "Oculus", "deviceModel": "Quest 3",
                "osName": "Android", "osVersion": "12", "hl": "en", "gl": "US"},
        "ua": "com.google.android.apps.youtube.vr.oculus/1.60.19 (Linux; U; Android 12; en_US; Quest 3) gzip",
    },
    {
        "name": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
        "ctx": {"clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
                "clientVersion": "2.0", "hl": "en", "gl": "US"},
        "ua": "Mozilla/5.0 (PlayStation; PlayStation 5/2.26) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0 Safari/605.1.15",
    },
]


async def search_video_id(query: str, diag: list | None = None) -> str | None:
    """videoId первого видео YouTube Music по InnerTube-поиску.
    Нужен на Render: yt-dlp с датацентровых IP часто ловит бот-чек,
    а InnerTube-клиенты — нет (search → player → прямой googlevideo-URL)."""
    if not query:
        return None
    from core.config import settings
    notes = diag if diag is not None else []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8),
                                     follow_redirects=True,
                                     proxy=settings.PROXY_URL or None) as c:
            r = await c.post(SEARCH_URL, params={"prettyPrint": "false"},
                             json={"context": {"client": SEARCH_CTX},
                                   "query": query},
                             headers={"Content-Type": "application/json",
                                      "Accept-Language": "en",
                                      "Origin": "https://music.youtube.com",
                                      "User-Agent": SEARCH_UA})
            if r.status_code != 200:
                notes.append(f"search: HTTP {r.status_code}")
                return None
            d = r.json()

            def walk(node) -> str | None:
                """Глубокий обход в поисках первого playlistItemData.videoId."""
                if isinstance(node, dict):
                    vid = (node.get("playlistItemData") or {}).get("videoId") \
                        or (node.get("musicResponsiveListItemRenderer") or {}).get("playlistItemData", {}).get("videoId")
                    if isinstance(vid, str) and len(vid) == 11:
                        return vid
                    for v in node.values():
                        got = walk(v)
                        if got:
                            return got
                elif isinstance(node, list):
                    for v in node:
                        got = walk(v)
                        if got:
                            return got
                return None

            vid = walk(d)
            if vid:
                return vid
            notes.append("search: нет videoId в ответе")
    except Exception as e:  # noqa: BLE001
        notes.append(f"search: {type(e).__name__}")
    log.info("innertube search «%s»: %s", query[:60], "; ".join(notes))
    return None


async def audio_url(video_id: str, diag: list | None = None) -> dict | None:
    """Первый рабочий прямой аудио-URL: {url, mime, bitrate, itag, size, client}."""
    if not video_id:
        return None
    from core.config import settings
    notes = diag if diag is not None else []
    base_headers = {
        "Content-Type": "application/json",
        "Accept-Language": "en",
        "Origin": "https://www.youtube.com",
        "X-Goog-Api-Format-Version": "2",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8),
                                     follow_redirects=True,
                                     proxy=settings.PROXY_URL or None) as c:
            for cl in CLIENTS:
                try:
                    r = await c.post(PLAYER_URL, json={
                        "context": {"client": cl["ctx"]},
                        "videoId": video_id,
                        "contentCheckOk": True,
                        "racyCheckOk": True,
                    }, headers={**base_headers, "User-Agent": cl["ua"]})
                    if r.status_code != 200:
                        notes.append(f"{cl['name']}: HTTP {r.status_code}")
                        continue
                    d = r.json()
                    st = (d.get("playabilityStatus") or {}).get("status")
                    if st != "OK":
                        reason = str((d.get("playabilityStatus") or {}).get("reason") or "")[:60]
                        notes.append(f"{cl['name']}: {st} {reason}")
                        continue
                    fmts = ((d.get("streamingData") or {}).get("adaptiveFormats")) or []
                    cands = []
                    for f in fmts:
                        u = f.get("url") or ""
                        mime = (f.get("mimeType") or "").split(";")[0]
                        if not u.startswith("http") or not mime.startswith("audio/"):
                            continue
                        cands.append({
                            "url": u, "mime": mime,
                            "bitrate": int(f.get("bitrate") or 0),
                            "itag": f.get("itag"),
                            "size": int(f.get("contentLength") or 0),
                            "client": cl["name"],
                        })
                    if not cands:
                        notes.append(f"{cl['name']}: нет прямых аудио-URL")
                        continue
                    m4a = [x for x in cands if x["mime"] == "audio/mp4"]
                    pool = sorted(m4a or cands, key=lambda x: x["bitrate"], reverse=True)
                    best = pool[0]
                    if await _alive(c, best["url"]):
                        notes.append(f"{cl['name']}: OK itag={best['itag']} {best['bitrate']}bps")
                        return best
                    notes.append(f"{cl['name']}: URL не отдаёт байты")
                except Exception as e:  # noqa: BLE001
                    notes.append(f"{cl['name']}: {type(e).__name__}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"client: {type(e).__name__}")
    log.info("innertube %s: %s", video_id, "; ".join(notes))
    return None


async def _alive(c: httpx.AsyncClient, url: str) -> bool:
    """Проверяем, что URL реально отдаёт аудио-байты (дешёвый Range-запрос)."""
    try:
        r = await c.get(url, headers={"Range": "bytes=0-2047",
                                      "User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.status_code in (200, 206) and len(r.content) > 0
    except Exception:
        return False
