"""Парсер ссылок: определяет сервис и извлекает id плейлиста/трека."""
import re
from dataclasses import dataclass, field

@dataclass
class Link:
    service: str                 # yandex | spotify | zvuk | youtube
    kind: str                    # playlist | track | album | artist
    id: str | None = None
    extras: dict = field(default_factory=dict)

YANDEX_HOST = r"music\.yandex\.[a-z]{2,6}"

YANDEX_PAT = re.compile(
    YANDEX_HOST + r"/users/(?P<owner>[\w\-.]+)"
    r"/(?:playlists/(?P<plid>\d+)|album/(?P<alb>\d+)|track/(?P<trk>\d+))")
# iframe-эмбеды: /iframe/playlist/<owner>/<kind>, /iframe/album/<id>, /iframe/track/<id>
YANDEX_IFRAME_PAT = re.compile(
    YANDEX_HOST + r"/iframe/(?P<ikind>playlist|album|track)/(?P<iowner>[\w\-.]+)/(?P<iid>\d+)/?")
# новый формат шэринга: /playlists/<uuid> — префиксы ps. (обычные) и lk. («Мне нравится»)
YANDEX_UUID_PAT = re.compile(
    YANDEX_HOST + r"/(?:playlists|playlist)/(?P<uuid>(?:ps\.|lk\.)?[0-9a-fA-F-]{8,64}|\d+)")
SPOTIFY_PAT = re.compile(
    r"(?:(?:open\.)?spotify\.com/(?:intl-[a-z]+/)?|spotify:)"
    r"(?P<kind>playlist|track|album|artist)[/:](?P<id>[A-Za-z0-9]+)")
ZVUK_PAT = re.compile(
    r"zvuk\.com/(?:playlist/(?P<pl>\w+)|track/(?P<trk>\d+)|(?:album|release)/(?P<alb>\d+))")
YOUTUBE_PAT = re.compile(
    r"(?:youtube\.com/playlist\?|music\.youtube\.com/playlist\?)[^\s]*?list=(?P<pl>[\w\-]+)")

def parse(url: str) -> Link | None:
    url = (url or "").strip()
    m = SPOTIFY_PAT.search(url)
    if m:
        return Link("spotify", m.group("kind"), m.group("id"))
    m = YANDEX_PAT.search(url)
    if m:
        if m.group("plid"):
            return Link("yandex", "playlist", m.group("plid"), {"owner": m.group("owner")})
        if m.group("trk"):
            return Link("yandex", "track", m.group("trk"))
        if m.group("alb"):
            return Link("yandex", "album", m.group("alb"))
    m = YANDEX_IFRAME_PAT.search(url)
    if m:
        if m.group("ikind") == "playlist":
            return Link("yandex", "playlist", m.group("iid"), {"owner": m.group("iowner")})
        return Link("yandex", m.group("ikind"), m.group("iid"))
    m = YANDEX_UUID_PAT.search(url)
    if m:
        return Link("yandex", "playlist", m.group("uuid"), {"uuid_mode": True})
    m = ZVUK_PAT.search(url)
    if m:
        if m.group("pl"):
            return Link("zvuk", "playlist", m.group("pl"))
        if m.group("trk"):
            return Link("zvuk", "track", m.group("trk"))
        if m.group("alb"):
            return Link("zvuk", "release", m.group("alb"))
    m = YOUTUBE_PAT.search(url)
    if m:
        return Link("youtube", "playlist", m.group("pl"))
    return None
