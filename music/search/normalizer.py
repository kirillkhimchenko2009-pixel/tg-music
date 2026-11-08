"""Нормализация поисковых запросов, версии, разбор ссылок, похожесть."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlsplit

from ..enums import TrackVersion

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_DASH_RE = re.compile(r"\s+[-\u2013\u2014\u2015]\s+")
_BRACKET_RE = re.compile(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]")
_FEAT_RE = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s+([^)\]\-]+)", re.I)
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_SPACES_RE = re.compile(r"\s+")
_NOISE_RE = (
    re.compile(r"\bofficial\s+(?:music\s+)?(?:video|audio|lyric(?:s)?\s+video|visuali[sz]er|clip)\b", re.I),
    re.compile(r"\b(?:lyrics?|lyric\s+video|visuali[sz]er|audio|video|full\s+album|hd|hq|4k|8k)\b", re.I),
    re.compile(r"\bprod(?:uced)?\.?\s+by\s+[^)\]\-]+", re.I),
    re.compile(r"\b(?:download|free\s+download)\b", re.I),
)
_VERSION_RULES: tuple[tuple[TrackVersion, re.Pattern], ...] = (
    (TrackVersion.REMIX, re.compile(r"\b(?:re[-\s]?mix|rmx|bootleg|flip|mashup)\b", re.I)),
    (TrackVersion.SPEED, re.compile(r"\b(?:sped\s*up|speed\s*up|nightcore|slowed(?:\s*\+?\s*reverb)?)\b", re.I)),
    (TrackVersion.LIVE, re.compile(r"\b(?:live|concert|unplugged)\b", re.I)),
    (TrackVersion.ACOUSTIC, re.compile(r"\bacoustic\b", re.I)),
    (TrackVersion.INSTRUMENTAL, re.compile(r"\b(?:instrumental|karaoke|backing\s+track)\b", re.I)),
    (TrackVersion.COVER, re.compile(r"\b(?:cover|tribute)\b", re.I)),
    (TrackVersion.EXTENDED, re.compile(r"\bextended\b", re.I)),
    (TrackVersion.RADIO_EDIT, re.compile(r"\bradio\s+(?:edit|version)\b", re.I)),
    (TrackVersion.DEMO, re.compile(r"\bdemo\b", re.I)),
)
_STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
              "official", "video", "audio", "lyrics", "feat", "ft", "featuring"}


def normalize_text(value: str) -> str:
    s = unicodedata.normalize("NFKD", value or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = unicodedata.normalize("NFKC", s).lower().replace("ё", "е")
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return _SPACES_RE.sub(" ", s).strip()


def normalize_identity(value: str) -> str:
    return normalize_text(value)


def similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb:
        inter = len(ta & tb)
        containment = inter / min(len(ta), len(tb))
        jaccard = inter / len(ta | tb)
    else:
        containment = jaccard = 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    return round(max(seq, containment * 0.92, jaccard * 1.05 if jaccard > 0.8 else jaccard), 4)


def detect_version(text: str) -> tuple[TrackVersion, str]:
    for version, rx in _VERSION_RULES:
        match = rx.search(text or "")
        if not match:
            continue
        detail = ""
        for group in _BRACKET_RE.findall(text or ""):
            if rx.search(group):
                detail = group.strip()
                break
        if not detail:
            start = max(0, match.start() - 40)
            detail = text[start:match.end() + 40].strip(" -()[]")
        return version, detail
    return TrackVersion.ORIGINAL, ""


def clean_title(title: str) -> str:
    s = title or ""
    # убираем скобки с версией/фитом/шумом, остальные оставляем
    def repl(match: re.Match) -> str:
        inside = match.group(1)
        if (any(rx.search(inside) for _, rx in _VERSION_RULES)
                or _FEAT_RE.search(inside)
                or any(rx.search(inside) for rx in _NOISE_RE)):
            return " "
        return match.group(0)

    s = _BRACKET_RE.sub(repl, s)
    s = re.sub(r"\s+[-–—]\s+(?:re[-\s]?mix|rmx|live|acoustic|instrumental|"
               r"extended|radio\s+edit|demo|cover|sped\s*up|slowed).*$", "", s, flags=re.I)
    s = _FEAT_RE.sub("", s)
    for rx in _NOISE_RE:
        s = rx.sub(" ", s)
    s = s.replace('"', "").replace("“", "").replace("”", "")
    s = re.sub(r"\s*[\(\[]\s*[\)\]]", " ", s)
    return _SPACES_RE.sub(" ", s).strip(" -–—_. ")


@dataclass(slots=True)
class NormalizedQuery:
    raw: str
    text: str = ""
    title: str = ""
    artist: str = ""
    version: TrackVersion = TrackVersion.ORIGINAL
    version_detail: str = ""
    feat: list[str] = field(default_factory=list)
    year: int | None = None
    source_url: str = ""
    provider_hint: str = ""
    external_id_hint: str = ""
    is_url: bool = False

    @property
    def search_text(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} {self.title}".strip()
        return self.text or self.title or self.raw

    @property
    def display(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist and self.title else (self.text or self.raw)


def _strip_url(text: str) -> str:
    return _URL_RE.sub(" ", text or "").strip()


def _url_hint(url: str) -> tuple[str, str]:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "", ""
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    if host == "archive.org" or host.endswith(".archive.org"):
        m = re.match(r"/(?:details|download)/([^/]+)", path)
        return ("archive", m.group(1)) if m else ("", "")
    if host == "open.spotify.com":
        m = re.match(r"/track/([A-Za-z0-9]+)", path)
        return ("spotify", m.group(1)) if m else ("", "")
    if host == "spotify.com" or host.endswith(".spotify.com"):
        m = re.match(r"/track/([A-Za-z0-9]+)", path)
        return ("spotify", m.group(1)) if m else ("", "")
    if host == "jamendo.com" or host.endswith(".jamendo.com"):
        m = re.match(r"/track/([A-Za-z0-9]+)", path)
        return ("jamendo", m.group(1)) if m else ("", "")
    if host == "bandcamp.com" or host.endswith(".bandcamp.com"):
        return "bandcamp", url
    if host == "soundcloud.com" or host.endswith(".soundcloud.com"):
        return "soundcloud", url
    return "", ""


def parse(value: str) -> NormalizedQuery:
    raw = _SPACES_RE.sub(" ", (value or "").strip())
    url_match = _URL_RE.search(raw)
    if url_match:
        url = url_match.group(0).rstrip(").,;\"'")
        provider, external = _url_hint(url)
        return NormalizedQuery(raw=raw, text=url, title=url, source_url=url,
                               provider_hint=provider, external_id_hint=external,
                               is_url=True)

    text = _strip_url(raw)
    # убираем мусорные расширения и служебные пометки
    text = re.sub(r"\.(?:mp3|flac|wav|m4a|aac|ogg|opus|webm|mp4)\s*$", "", text, flags=re.I)
    work = text
    text = re.sub(r"[“”„\"]", "", text)
    text = _SPACES_RE.sub(" ", text).strip(" -–—_.|")

    version, version_detail = detect_version(text)
    feat = []
    for match in _FEAT_RE.finditer(text):
        names = [x.strip(" ,") for x in re.split(r"[,&]| and ", match.group(1)) if x.strip(" ,")]
        feat.extend(names)
    feat = list(dict.fromkeys(feat))

    title_part = text
    artist = ""
    dash = _DASH_RE.search(text)
    if dash:
        left, right = text[:dash.start()].strip(), text[dash.end():].strip()
        if left and right and len(left) <= 80:
            artist, title_part = left, right
    if not artist:
        by = re.match(r"^(.+?)\s+by\s+(.+)$", text, re.I)
        if by and not _DASH_RE.search(text):
            title_part, artist = by.group(1).strip(), by.group(2).strip()

    title = clean_title(title_part)
    artist = clean_title(artist)
    if not title:
        title = clean_title(text) or text
    if not artist:
        # запрос без явного исполнителя: title = вся строка, artist пустой
        title = clean_title(text) or title
    year = None
    ym = _YEAR_RE.search(work)
    if ym:
        year = int(ym.group(1))
    return NormalizedQuery(raw=raw, text=text, title=title, artist=artist,
                           version=version, version_detail=version_detail,
                           feat=feat, year=year, is_url=False)


def duration_score(a: int, b: int) -> float:
    if not a or not b:
        return 0.5
    diff = abs(int(a) - int(b))
    if diff <= 3:
        return 1.0
    if diff <= 8:
        return 0.9
    if diff <= 15:
        return 0.7
    if diff <= 30:
        return 0.45
    return max(0.0, 1.0 - diff / 120.0)


def score_candidate(query: NormalizedQuery, candidate) -> float:
    if query.is_url:
        return 1.0
    c_title = getattr(candidate, "title", "") or ""
    c_artist = getattr(candidate, "artist", "") or ""
    if query.artist and query.title:
        score = 0.55 * similarity(query.title, c_title) + 0.45 * similarity(query.artist, c_artist)
    else:
        q = query.text or query.title
        score = max(similarity(q, c_title), 0.65 * similarity(q, f"{c_artist} {c_title}"))
    c_version = getattr(candidate, "version", TrackVersion.ORIGINAL)
    if c_version == query.version:
        score += 0.12
    elif query.version is not TrackVersion.ORIGINAL and c_version is TrackVersion.ORIGINAL:
        score -= 0.12
    duration = int(getattr(candidate, "duration", 0) or 0)
    if duration >= 30:
        score += 0.03
    elif 0 < duration < 10:
        score -= 0.1
    if query.feat:
        joined = f"{c_artist} {c_title}".lower()
        if any(n.lower() in joined for n in query.feat):
            score += 0.05
    if getattr(candidate, "permission", None) and candidate.permission.can_download:
        score += 0.03
    if query.year and getattr(candidate, "year", None) == query.year:
        score += 0.02
    return max(0.0, min(1.0, round(score, 4)))


def parse_duration(value: str | int | None) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
        total = 0
        for p in parts:
            total = total * 60 + int(p)
        return total
    return 0


__all__ = ["NormalizedQuery", "parse", "normalize_text", "normalize_identity",
           "similarity", "detect_version", "clean_title", "duration_score",
           "score_candidate", "parse_duration"]
