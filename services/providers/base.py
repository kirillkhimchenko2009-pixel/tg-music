"""Общие структуры: TrackMeta (метаданные трека)."""
from dataclasses import dataclass

@dataclass
class TrackMeta:
    source: str              # ytmusic | yandex | zvuk
    external_id: str
    title: str
    artist: str = ""
    album: str | None = None
    year: int | None = None
    isrc: str | None = None
    duration: int = 0        # сек
    cover_url: str | None = None

    def display(self) -> str:
        head = f"{self.artist} — {self.title}" if self.artist else self.title
        return head

    def key(self) -> tuple[str, str]:
        return (self.source, self.external_id)

def parse_duration(s: str | None) -> int:
    """'3:45' | '1:02:03' -> сек"""
    if not s:
        return 0
    parts = [int(p) for p in str(s).strip().split(":")]
    n = 0
    for p in parts:
        n = n * 60 + p
    return n
