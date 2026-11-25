"""Матчинг треков между сервисами: точный по ISRC, иначе fuzzy по тексту.

Релевантность для русскоязычного поиска (мастер-промпт §6):
- Unicode NFKD-нормализация, кавычки/типографика → нейтральные символы
- транслитерация кириллицы (Кино ↔ Kino, Группа крови ↔ Gruppa Krovi)
- учёт версий: feat/remix/radio edit/live/инструментал НЕ склеиваются
  с оригиналом — разные версии всегда остаются разными треками.
"""
import difflib
import re
import unicodedata

from services.providers.base import TrackMeta

_STOP = re.compile(r"[^\wа-яё ]+", re.IGNORECASE)
_QUOTE = {  # типографские кавычки/дефисы → нейтральные
    "«": "", "»": "", "„": "", "“": "", "”": "", "’": "", "‘": "",
    "–": "-", "—": "-", "−": "-",
}
# транслитерация кириллица → латиница (ГОСТ-подобная, практичная для поиска)
_XLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "i", "є": "e", "ґ": "g",
}

# маркеры версий (ремиксы/лайвы/радио-версии — отдельные произведения)
_VERSION = re.compile(
    r"\b(remix(es|ed)?|mix(es|ed)?|bootleg|radio edit|radio version|single version|"
    r"album version|extended|club|dub|live|acoustic|unplugged|instrumental|"
    r"feat|ft|featuring|vers?\.|version|edit|cover)\b", re.IGNORECASE)

# чистые релизы (лейблы под РФ-закон 436-ФЗ выпускают замазанные дубли):
# при прочих равных выбираем оригинал — clean-мастер звучит «замазанно»
_CLEAN_TITLE = re.compile(
    r"[\(\[]?\s*(clean|clean version|clean edit|censored|unclean)\b", re.IGNORECASE)


def _fold(s: str) -> str:
    """Общая предобработка: NFKD, диакритика, кавычки, дефисы, мусор, пробелы."""
    s = unicodedata.normalize("NFKD", s or "")
    # сочетаемые маркеры убираем ДО замены мусора пробелами, иначе «Ёлка»
    # распадается на «е лка» (маркер → пробел → граница слова)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ё", "е").replace("Ё", "Е")     # на случай уже разложенной формы
    for k, v in _QUOTE.items():
        s = s.replace(k, v)
    s = s.lower()
    s = _STOP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm(s: str | None) -> str:
    """Нормализованный текст (кириллица сохраняется как есть)."""
    return _fold(s or "")


def norm_xlit(s: str | None) -> str:
    """Нормализованный текст с транслитерацией кириллицы в латиницу."""
    n = _fold(s or "")
    return "".join(_XLIT.get(ch, ch) for ch in n)


def version_of(s: str | None) -> str:
    """Каноническая метка версии трека (для НЕ слияния оригинала с ремиксом)."""
    txt = norm(s or "")
    tags = sorted(set(m.group(1).lower() for m in _VERSION.finditer(txt)))
    return " ".join(tags) if tags else ""


def similarity(a: TrackMeta, b: TrackMeta) -> float:
    """0..1 — совпадение треков по тексту и (примерно) длительности.

    Текст сравнивается дважды: как есть и в транслитерации — совпадение
    «Кино — Группа крови» с «Kino — Gruppa Krovi» считается полным.
    Разные версии (remix vs оригинал) штрафуются отдельным штрафом.
    """
    best = 0.0
    for fx in (norm, norm_xlit):
        na, nb = fx(f"{a.artist} {a.title}"), fx(f"{b.artist} {b.title}")
        if not na or not nb:
            continue
        best = max(best, difflib.SequenceMatcher(None, na, nb).ratio())
    dur_penalty = 0.0
    if a.duration and b.duration and abs(a.duration - b.duration) > 5:
        dur_penalty = 0.15 * min(abs(a.duration - b.duration) / 60, 1.0)
    va, vb = version_of(f"{a.artist} {a.title}"), version_of(f"{b.artist} {b.title}")
    ver_penalty = 0.0
    if va != vb and (va or vb):
        # оригинал vs ремикс/лайв — всегда разные треки (мастер-промпт §6)
        ver_penalty = 0.25
    # clean-релиз против равного оригинала — небольшой штраф (0.1):
    # если это единственный кандидат, он всё равно пройдёт порог 0.62,
    # но там, где есть оригинал, замазанный мастер не выиграет
    if _CLEAN_TITLE.search(b.title or "") and not _CLEAN_TITLE.search(a.title or ""):
        ver_penalty += 0.10
    return max(0.0, best - dur_penalty - ver_penalty)


def best_match(target: TrackMeta, candidates: list[TrackMeta],
               min_score: float = 0.62) -> TrackMeta | None:
    """Лучший кандидат по ISRC (если есть с обеих сторон), иначе по score."""
    for c in candidates:
        if target.isrc and c.isrc and target.isrc.upper() == c.isrc.upper():
            return c
    scored = [(similarity(target, c), c) for c in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] >= min_score else None
