"""Массовая загрузка списком: txt-файл."""
import re
import unicodedata

from aiogram import F, Router, types

from bot.batch import start_batch

router = Router(name="bulk")

NUMBER_RE = re.compile(r"^\s*[\[(]?\d{1,3}[\])).:–-]+\s*")
SEP = [" — ", " - ", " – ", " − ", " -", "—", "-", "–", "|"]

def _split_line(line: str) -> tuple[str, str]:
    line = NUMBER_RE.sub("", unicodedata.normalize("NFC", line)).strip()
    for sep in SEP:
        if sep in line:
            a, b = line.split(sep, 1)
            if a.strip() and b.strip():
                return a.strip()[:128], b.strip()[:128]
    return "", line[:192]

def _lines_to_metas(text: str, limit: int = 300):
    metas = []
    seen = set()
    for raw in text.splitlines():
        line = raw.strip("•·* \t")
        if not line or line.startswith("#"):
            continue
        line = NUMBER_RE.sub("", line).strip()
        if len(line) < 2:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        artist, title = _split_line(line)
        metas.append({
            "source": "text",
            "external_id": f"t{abs(hash(key)) % 10**12}",
            "title": title or line,
            "artist": artist,
        })
        if len(metas) >= limit:
            break
    return metas

@router.message(F.document)
async def on_document(message: types.Message):
    doc = message.document
    mime = doc.mime_type or ""
    if mime and not (mime.startswith("text") or mime == "application/txt"):
        await message.answer("Пришли файл в формате <b>.txt</b> — по треку на строку.\n"
                             "Формат строк: <code>Исполнитель — Название</code> или просто <code>Название</code>")
        return
    file = await message.bot.get_file(doc.file_id)
    buf = await message.bot.download_file(file.file_path)
    raw = (buf.read() if hasattr(buf, "read") else b"") or b""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="ignore")
    metas = _lines_to_metas(text)
    if not metas:
        await message.answer("Не нашёл ни одной строки. Формат: <code>Исполнитель — Название</code>, строка на трек.")
        return
    from services.providers.base import TrackMeta
    title = doc.file_name or "Список"
    await start_batch(message, title, [TrackMeta(**m) for m in metas])
