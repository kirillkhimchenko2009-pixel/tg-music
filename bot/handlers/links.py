"""Ссылки на плейлисты/треки: Яндекс Музыка, Spotify, Звук, YouTube."""
import logging
import re

from aiogram import Router, types
from aiogram.filters import BaseFilter

from bot import runtime
from bot.batch import start_batch
from bot.keyboards import main_menu
from bot.state import EXT_CACHE
from core.config import settings
from services.authstore import get_token
from services.links import parse as parse_link
from services.providers.base import TrackMeta
from services.providers import yandex as ymx
from services.providers import soundcloud as scx
from services.providers import zvuk as zv
from services.providers.spotify import SpotifyProvider
from services.providers.youtube import YouTubeProvider

router = Router(name="links")
log = logging.getLogger("bot.links")
yt = YouTubeProvider()

class IsLink(BaseFilter):
    """Пропускает только сообщения-ссылки (иначе роутер съедает весь текст бота)."""
    async def __call__(self, message: types.Message) -> bool:
        return bool(parse_link(message.text or ""))

class IsLinkLike(BaseFilter):
    """Сообщение похоже на ссылку, но парсер её не понял."""
    async def __call__(self, message: types.Message) -> bool:
        text = message.text or ""
        if parse_link(text):
            return False
        return bool(re.search(
            r"music\.yandex\.|ym://|zvuk\.com|soundcloud\.com|" +
            r"(?:open\.)?spotify\.com|spotify:",
            text, re.IGNORECASE))

async def _fetch(link, ym_tok: str, zv_tok: str) -> tuple[str, list[TrackMeta]] | None:
    """Возвращает (заголовок, треки) для ссылки либо None."""
    if link.service == "yandex" and link.kind == "playlist":
        if link.extras.get("uuid_mode"):
            # публичные плейлисты нового формата читаются анонимно
            return await ymx.anon_playlist_by_uuid(link.id)
        login = link.extras.get("owner", "")
        if login in ("", "me") and not ym_tok:
            raise RuntimeError(
                "Это твой личный плейлист («Мне нравится»/Любимое). В приложении нажми "
                "⋮ → «Поделиться» → скопируй ссылку вида music.yandex.ru/playlists/… "
                "и пришли сюда — публичные читаются без токена. Либо подключи аккаунт: /token")
        try:
            return await ymx.anon_playlist_by_login(login, link.id)
        except Exception as anon_err:  # noqa: BLE001
            if ym_tok:
                return await ymx.get_public_playlist(login, link.id, ym_tok)
            raise anon_err
    if link.service == "spotify" and link.kind == "playlist":
        sp = SpotifyProvider(settings.SPOTIFY_CLIENT_ID, settings.SPOTIFY_CLIENT_SECRET)
        return await sp.playlist(link.id)
    if link.service == "zvuk" and link.kind == "playlist":
        return await zv.get_playlist(link.id, token=zv_tok or None)
    if link.service == "soundcloud" and link.kind == "playlist":
        res = await scx.playlist_by_url(link.id)
        if res:
            return res
        return None
    if link.service == "youtube" and link.kind == "playlist":
        metas = await yt.playlist(link.id)
        return (f"YouTube · {len(metas)} треков", metas)
    return None

async def _fetch_track(link, ym_tok: str, zv_tok: str) -> TrackMeta | None:
    if link.service == "spotify" and link.kind == "track":
        sp = SpotifyProvider(settings.SPOTIFY_CLIENT_ID, settings.SPOTIFY_CLIENT_SECRET)
        return await sp.track(link.id)
    if link.service == "yandex" and link.kind == "track":
        return await ymx.track_by_id(ym_tok, link.id)
    if link.service == "zvuk" and link.kind == "track":
        return await zv.track_meta(link.id, token=zv_tok or None)
    return None

@router.message(IsLink())
async def on_link(message: types.Message):
    link = parse_link(message.text or "")
    if not link:
        return  # не ссылка — пусть обрабатывают другие роутеры
    log.info("link from %s: %s -> %s", message.from_user.id,
             (message.text or "")[:120], link)
    uid = message.from_user.id
    async with runtime.get_session() as s:
        await runtime.ensure_user(s, uid, message.from_user.username,
                                  message.from_user.first_name or "")
        ym_tok = await get_token(s, uid, "ym")
        zv_tok = await get_token(s, uid, "zvuk")
    from bot import fx
    fx.typing(message.chat.id, message.bot)
    status = await message.answer("🔗 Изучаю ссылку…")
    try:
        result = await _fetch(link, ym_tok, zv_tok)
    except Exception as e:  # noqa: BLE001
        await status.edit_text(
            "❌ Не смог прочитать ссылку.\n" + str(e)[:180] +
            ("\n\nНужен токен сервиса? Отправь /token" if link.service in ("yandex", "zvuk") else ""))
        return
    if not result:
        try:
            t = await _fetch_track(link, ym_tok, zv_tok)
        except Exception:  # noqa: BLE001
            t = None
        if t:
            await status.delete()
            await start_batch(message, t.display(), [t])
            return
        await status.edit_text(
            "⚠ Пока поддерживаются публичные плейлисты.\n"
            "Для приватных/полных нужно подключить аккаунт: команда <code>/token</code>")
        return

    title, metas = result
    if not metas:
        await status.edit_text("⚠ В этом плейлисте нет доступных треков.")
        return
    await status.delete()

    # Импорт в приложение: плейлист должен быть виден во вкладке «Плейлисты»
    pl_id = None
    added = 0
    try:
        from services.catalog import upsert_track
        from core.models import PlaylistTrack
        from sqlalchemy import select
        async with runtime.get_session() as s:
            await runtime.ensure_user(s, uid, message.from_user.username,
                                      message.from_user.first_name or "")
            pl = await runtime.get_or_create_playlist(s, uid, (title or "Импорт")[:120])
            already = len(list((await s.execute(
                select(PlaylistTrack).where(
                    PlaylistTrack.playlist_id == pl.id))).scalars()))
            for m in metas:
                t = await upsert_track(s, m)
                if t is None:
                    continue
                exists = (await s.execute(
                    select(PlaylistTrack).where(
                        PlaylistTrack.playlist_id == pl.id,
                        PlaylistTrack.track_id == t.id))).scalar_one_or_none()
                if exists is None:
                    s.add(PlaylistTrack(playlist_id=pl.id, track_id=t.id,
                                        position=already + added))
                    added += 1
            await s.commit()
            pl_id = pl.id
    except Exception:
        log.exception("не удалось создать плейлист из ссылки")

    if pl_id:
        from bot import fx
        note = ("<b>📋 Плейлист добавлен в приложение:</b> "
                + fx.esc_html(title or "Импорт") +
                f"\n🎵 Треков: {added + already}"
                + ("\n<i>Новые треки добавлены, дубликаты пропущены.</i>"
                   if already and added else
                   "\n<i>Все эти треки уже были в этом плейлисте.</i>" if already else ""))
        cover = next((m.cover_url for m in metas if m.cover_url), None)
        sent = False
        if cover:
            try:
                await message.answer_photo(photo=cover, caption=note,
                                           reply_markup=main_menu())
                sent = True
            except Exception:  # noqa: BLE001
                pass          # обложка битая/приватная — текстом
        if not sent:
            await message.answer(note, reply_markup=main_menu())
        await fx.react(message.bot, message.chat.id,
                       message.message_id, fx.FIRE)
    else:
        # фолбэк: старое поведение — батч-скачивание
        await start_batch(message, title, metas)

@router.message(IsLinkLike())
async def on_unparsed_link(message: types.Message):
    """Ссылка есть, но не распозналась — отвечаем внятно вместо молчания."""
    log.info("unparsed link-like from %s: %r", message.from_user.id,
             (message.text or "")[:160])
    await message.answer(
        "🤔 Не понял эту ссылку.\n\n"
        "Как дать мне плейлист Яндекса: открой его → <b>⋮ → Поделиться → Копировать ссылку</b> "
        "и пришли сюда одной строкой (вида <code>music.yandex.ru/playlists/…</code> или "
        "<code>music.yandex.ru/users/…/playlists/…</code>). "
        "Ссылки вида <code>ym://…</code> (внутренние диплинки) не подходят.")
