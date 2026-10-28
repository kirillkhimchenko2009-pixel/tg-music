"""Проверка parse_link на формах ссылок Яндекс.Музыки + anon-фетыч kind=3."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.links import parse

candidates = [
    "https://music.yandex.ru/users/587118110/playlists/3",      # Мне нравится по uid
    "https://music.yandex.ru/users/iva.noya/playlists/1008",    # обычный
    "https://music.yandex.ru/users/me/playlists/3",             # псевдовладелец me
    "https://music.yandex.com/playlists/407d0494-d589-84ff-4824-ebb99bec315f",
    "https://music.yandex.ru/playlists/ps.407d0494-d589-84ff-4824-ebb99bec315f",
    "https://music.yandex.by/users/iva.noya/playlists/1008",
    "https://music.yandex.ru/users/587118110/playlists/3/track/110734923",
    "https://music.yandex.ru/iframe/playlist/587118110/3",
    "https://music.yandex.ru/playlist/407d0494-d589-84ff-4824-ebb99bec315f",
    "ym://offer/playlist/3",                                    # диплинк приложения
    "https://music.yandex.ru/users/587118110/playlists/liked",
]
for u in candidates:
    link = parse(u)
    print(f"{u!r:<78} -> {link!r}")
