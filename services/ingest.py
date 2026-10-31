"""Инжест треков: превращает TrackMeta (откуда угодно) в скачанный файл + запись в каталоге."""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.models import Track
from services import matching, pipeline
from services.authstore import get_token
from services.providers.base import TrackMeta
from services.providers.youtube import YouTubeProvider
from services.providers import zvuk as zv
from services.providers import zaycev as zay
from services.providers import hitmoz as hzm
from services.providers import hotplayer as hpl
from services.providers.yandex import YandexProvider

yt = YouTubeProvider()
log = logging.getLogger("ingest")

@dataclass
class Resolved:
    track: Track | None
    file_path: Path | None
    quality: str | None
    status: str                     # cached | downloaded | not_found
    error: str | None = None

async def _candidates(session: AsyncSession, user_id: int | None,
                      meta: TrackMeta, quality: str) -> list[tuple[TrackMeta, str]]:
    """Цепочка вариантов: свои источники по токенам → Звук → ЯМ → YouTube Music.
    Возвращает [(вариант_meta, token_или_''), ...]."""
    out: list[tuple[TrackMeta, str]] = []
    query = f"{meta.artist} {meta.title}".strip()

    # Свой источник — первым, но цепочку НЕ заканчиваем: если ЯМ/Звук не отдали
    # файл (нет аудио в каталоге, права, регион), подключаются фолбэки ниже.
    if meta.source == "zvuk":
        tok = await get_token(session, user_id, "zvuk")
        if tok:
            out.append((meta, tok))
    if meta.source == "yandex":
        tok = await get_token(session, user_id, "ym")
        if tok:
            out.append((meta, tok))

    # 1) Звук (если есть токен — там лучше качество, вплоть до FLAC)
    zv_tok = await get_token(session, user_id, "zvuk")
    if zv_tok and meta.source != "zvuk":
        try:
            cands = await zv.search(query, limit=6, token=zv_tok)
            m = matching.best_match(meta, cands)
            if m:
                out.append((m, zv_tok))
        except Exception:
            pass

    # 2) Яндекс Музыка (если есть токен и это не сам-источник)
    ym_tok = await get_token(session, user_id, "ym")
    if ym_tok and meta.source != "yandex":
        try:
            prov = YandexProvider(ym_tok)
            cands = await prov.search(query, limit=6)
            m = matching.best_match(meta, cands)
            # guard по названию и в строгом матче: цельное «артист название»
            # завышает скор при длинном имени артиста — иначе предложит
            # совершенно чужой трек того же исполнителя
            if m and _title_sim(meta.title, m.title) < 0.55:
                log.info("ям: кандидат %s отвергнут по названию (%.2f) для %s",
                         m.display(), _title_sim(meta.title, m.title),
                         meta.display())
                m = None
            if not m and cands:
                # широкий фолбэк: точное название может не пройти строгий порог
                # (транслит/фиты/издания) — берём лучшее по схожести, но строго
                # по артисту, чтобы не подсовывать песню чужого исполнителя
                pool = list(cands)
                q2 = _clean_query(query)
                if q2 and q2.lower() != query.lower().strip():
                    try:
                        pool += await prov.search(q2, limit=6)
                    except Exception:
                        pass
                m = matching.best_match(meta, pool, min_score=0.45)
                # «artist title» целиком завышает скор на общем имени артиста —
                # поэтому дополнительно требовать похожести самого названия
                if m and _title_sim(meta.title, m.title) < 0.6:
                    log.info("ям: кандидат %s отвергнут по названию (%.2f) для %s",
                             m.display(), _title_sim(meta.title, m.title),
                             meta.display())
                    m = None
                if m and not hzm.artist_guard(meta, m):
                    log.info("ям: кандидат %s отвергнут по артисту для %s",
                             m.display(), meta.display())
                    m = None
                if m:
                    log.info("широкий ям-матч для %s: %s",
                             meta.display(), m.display())
            if m:
                out.append((m, ym_tok))
        except Exception:
            log.exception("поиск на яндексе не удался")

    # 2.5) Прямые RU-сайты без токенов (быстрее YT): zaycev.net (mp3-320) → hitmoz → hotplayer
    for prov, name in ((zay, "zaycev"), (hzm, "hitmoz"), (hpl, "hotplayer")):
        try:
            cands = await prov.search(query, limit=6)
            m = matching.best_match(meta, cands, min_score=0.55)
            # guard по артисту — иначе сайты подсовывают однофамильцев/тёзок
            if m and not hzm.artist_guard(meta, m):
                log.info("%s: кандидат %s отвергнут по артисту", name, m.display())
                m = None
            if m:
                out.append((m, ""))
        except Exception:
            log.exception("поиск на %s не удался", name)

    # 3) YouTube Music: для ytmusic-источника сам трек — последний запасной
    #    вариант (с зарубежных IP YouTube часто ловит бот-чек, RU-сайты выше надёжнее)
    if meta.source == "ytmusic":
        out.append((meta, ""))
    else:
        try:
            out.extend(await _yt_candidates(meta, query))
        except Exception:
            log.exception("yt-поиск упал для %s", meta.display())
    return out


_JUNK = re.compile(r"\((?:[^)]*(?:feat|ft\.|official|video|audio|lyric|remaster|live)[^)]*)\)"
                   r"|\[[^\]]*\]|\s+feat\.?\s+.+$", re.IGNORECASE)


def _clean_query(q: str) -> str:
    q = _JUNK.sub(" ", q)
    return re.sub(r"\s+", " ", q).strip(" -–—:,")


def _title_sim(a: str, b: str) -> float:
    """Схожесть только названий: общий текст a+b завышает скор, когда
    артист длинный (префикс всегда совпадает)."""
    import difflib
    from services.matching import norm
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


async def _yt_candidates(meta: TrackMeta, query: str) -> list[tuple[TrackMeta, str]]:
    """Строгий матч (0.55) по обычному и очищенному запросу; затем широкий порог
    (0.42) как последний шанс; в конце добавляем запасного кандидата — если у
    первого упадёт yt-dlp, батч попробует его вместо пропуска."""
    out: list[tuple[TrackMeta, str]] = []
    queries = [query]
    q2 = _clean_query(query)
    if q2 and q2.lower() != query.lower().strip():
        queries.append(q2)
    pool: list[TrackMeta] = []
    for q in queries:
        try:
            cands = await yt.search(q, limit=6)
        except Exception:
            cands = []
        pool.extend(cands)
        m = matching.best_match(meta, cands, min_score=0.55)
        if m:
            out.append((m, ""))
            break
    if not out and pool:
        m = matching.best_match(meta, pool, min_score=0.42)
        if m:
            log.info("широкий матч для %s: %s", meta.display(), m.display())
            out.append((m, ""))
    if out:
        rest = [c for c in pool if c.external_id != out[0][0].external_id]
        scored = sorted(((matching.similarity(meta, c), c) for c in rest),
                        key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= 0.5:
            out.append((scored[0][1], ""))
    return out

async def resolve(session: AsyncSession, meta: TrackMeta, quality: str,
                  user_id: int | None = None, on_progress=None) -> Resolved:
    # 1) Трек уже в каталоге с кешем file_id?
    track = await _upsert(session, meta)
    if track.file_id:
        return Resolved(track=track, file_path=None, quality=track.quality, status="cached")

    # Уже скачан локально, но файл ещё не залит в ТГ
    if track.local_path and Path(track.local_path).exists():
        return Resolved(track=track, file_path=Path(track.local_path),
                        quality=track.quality, status="downloaded")

    # 2) Выбор источника и скачивание
    chain = await _candidates(session, user_id, meta, quality)
    if not chain:
        err = ("по запросу не нашлось совпадений (YouTube без токена; "
               "ЯМ/Звук дадут лучше, если добавить токен /token)")
        log.warning("skip %s: %s", meta.display(), err)
        return Resolved(track=track, file_path=None, quality=None,
                        status="not_found", error=err)

    for dl_meta, token in chain:
        res = await pipeline.download(dl_meta, quality, token, on_progress=on_progress)
        if not res:
            log.warning("skip %s: скачивание не удалось на %-6s (%s)",
                        meta.display(), dl_meta.source, dl_meta.display())
            continue
        dl_track = await _upsert(session, dl_meta)
        dl_track.quality = res.quality
        dl_track.size_bytes = res.size_bytes
        dl_track.local_path = res.path.as_posix()
        await session.commit()
        # удаляем заглушку из другого источника, если она без файла
        if dl_track.id != track.id and not (track.local_path or track.file_id):
            try:
                await session.delete(track)
                await session.flush()
            except Exception:
                pass
        return Resolved(track=dl_track, file_path=res.path,
                        quality=res.quality, status="downloaded")
    tried = ", ".join(dict.fromkeys(m.source for m, _ in chain))
    err = f"все источники не ответили (пробовали: {tried})"
    log.warning("skip %s: %s", meta.display(), err)
    return Resolved(track=track, file_path=None, quality=None,
                    status="not_found", error=err)

async def _upsert(session: AsyncSession, meta: TrackMeta) -> Track:
    from services.catalog import upsert_track
    return await upsert_track(session, meta)
