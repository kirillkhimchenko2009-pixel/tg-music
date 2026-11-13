"""Общие структуры: TrackMeta (метаданные трека)."""
from dataclasses import dataclass

@dataclass
class TrackMeta:
    source: str              # ytmusic | yandex | zvuk | audius | ...
    external_id: str
    title: str
    artist: str = ""
    album: str | None = None
    year: int | None = None
    isrc: str | None = None
    duration: int = 0        # сек
    cover_url: str | None = None
    explicit: bool = False   # метка 18+: у ЯМ такие треки бывают «чистыми» версиями
    page_url: str | None = None    # официальная страница (для OFFICIAL_LINK_ONLY и «источника»)
    license: str | None = None     # известная лицензия (CC-BY 4.0 и т.п.)
    downloadable: bool = True      # разрешена ли загрузка конкретного файла (не всего сайта!)

    def display(self) -> str:
        head = f"{self.artist} — {self.title}" if self.artist else self.title
        return head

    def key(self) -> tuple[str, str]:
        return (self.source, self.external_id)

def link_meta(source: str, provider_name: str, query: str, page_url: str) -> TrackMeta:
    """Честная карточка-ссылка для источников без API (OFFICIAL_LINK_ONLY):
    ведёт на официальную страницу поиска источника, без фиктивных результатов."""
    q = (query or "").strip()
    return TrackMeta(source=source,
                     external_id=("search:" + q)[:120],
                     title=f"{provider_name}: официальный поиск «{q}»",
                     artist="",
                     duration=0,
                     page_url=page_url,
                     downloadable=False)

def parse_duration(s: str | None) -> int:
    """'3:45' | '1:02:03' -> сек"""
    if not s:
        return 0
    parts = [int(p) for p in str(s).strip().split(":")]
    n = 0
    for p in parts:
        n = n * 60 + p
    return n
