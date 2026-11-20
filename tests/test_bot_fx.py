"""Виджеты Telegram: реакции-эмодзи, инлайн-поиск, фото-карточки, диплинки."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import fx, inline, runtime
from bot.batch import start_batch
from bot.handlers import playlists, start as start_handler


@pytest.fixture
async def fx_db(db_engine, monkeypatch):
    """In-memory SQLite со всеми таблицами; bot.runtime.get_session → неё."""
    from core.models import Base as CoreBase

    async with db_engine.begin() as conn:
        await conn.run_sync(CoreBase.metadata.create_all)
    maker = async_sessionmaker(db_engine, expire_on_commit=False,
                               class_=AsyncSession)
    monkeypatch.setattr("bot.runtime.get_session", maker)
    return maker


class _BotStub:
    def __init__(self, fail_react=False):
        self.reactions: list[tuple] = []
        self.actions: list[tuple] = []
        self.fail_react = fail_react

    async def set_message_reaction(self, chat_id, message_id, reaction, **kw):
        if self.fail_react:
            raise RuntimeError("telegram down")
        self.reactions.append((chat_id, message_id,
                               [r.emoji for r in reaction]))

    async def send_chat_action(self, chat_id, action, **kw):
        self.actions.append((chat_id, action))


class _MsgStub:
    def __init__(self, chat_id=100, from_id=200, message_id=5):
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=from_id, username="u", first_name="U")
        self.message_id = message_id
        self.bot = _BotStub()
        self.photos: list[tuple] = []
        self.texts: list[tuple] = []
        self.fail_photo = False

    async def answer_photo(self, photo, **kw):
        if self.fail_photo:
            raise RuntimeError("bad cover")
        self.photos.append((photo, kw))
        return SimpleNamespace(message_id=self.message_id + 1)

    async def answer(self, text, **kw):
        self.texts.append((text, kw))
        return SimpleNamespace(message_id=self.message_id + 2)


class _StatusStub:
    def __init__(self):
        self.edits: list[str] = []
        self.deleted = False

    async def edit_text(self, text, **kw):
        self.edits.append(text)

    async def delete(self):
        self.deleted = True


def _meta(i: int, cover: str | None = None):
    from services.providers.base import TrackMeta
    return TrackMeta(source="yandex", external_id=f"y:{i}",
                     title=f"Song {i}", artist="Artist", duration=180,
                     cover_url=cover)


# ------------------------------ реакции ------------------------------

class TestReact:
    async def test_sets_emoji(self):
        bot = _BotStub()
        await fx.react(bot, 100, 55, fx.OK)
        assert bot.reactions == [(100, 55, [fx.OK])]

    async def test_failures_swallowed(self):
        bot = _BotStub(fail_react=True)
        await fx.react(bot, 100, 55, fx.FIRE)      # не должно упасть
        assert bot.reactions == []

    async def test_no_message_id_is_noop(self):
        bot = _BotStub()
        await fx.react(bot, 100, None, fx.OK)
        assert bot.reactions == []

    async def test_typing_fire_and_forget(self):
        bot = _BotStub()
        t = fx.typing(100, bot)
        await t
        assert bot.actions == [(100, "typing")]


# --------------------------- инлайн-поиск ---------------------------

async def _seed_inline_tracks(maker):
    from core.models import Track
    async with maker() as s:
        cached = Track(source="yandex", external_id="y:1", title="Song One",
                       artist="A", file_id="fid-1")
        plain = Track(source="zaycev", external_id="z:2", title="Song Two",
                      artist="B", cover_url="https://c.example/2.jpg")
        nocov = Track(source="yandex", external_id="y:3", title="Other",
                      artist="C")
        s.add_all([cached, plain, nocov])
        await s.commit()
        return [cached.id, plain.id, nocov.id]


def test_playlist_web_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setattr(playlists.settings, "WEBAPP_URL",
                        "https://music.example/v2///", raising=False)
    assert playlists._web_url(12) == "https://music.example/v2/#/playlist/12"


class TestInline:
    async def test_db_search_cached_first(self, fx_db):
        await _seed_inline_tracks(fx_db)
        async with fx_db() as s:
            tracks = await inline.db_search(s, "song")
        assert len(tracks) == 2            # «Other» не подходит
        assert tracks[0].file_id == "fid-1"   # скачанный — вперёд

    async def test_db_search_empty_query(self, fx_db):
        async with fx_db() as s:
            assert await inline.db_search(s, "") == []
            assert await inline.db_search(s, "  ") == []

    async def test_results_cached_audio_and_article(self, fx_db, monkeypatch):
        await _seed_inline_tracks(fx_db)
        monkeypatch.setattr(inline.settings, "WEBAPP_URL",
                            "https://vibe.example/app", raising=False)
        async with fx_db() as s:
            tracks = await inline.db_search(s, "song")
        res = inline._results(tracks, "vibe_bot")
        assert len(res) == 2
        first = res[0]
        assert first.type == "audio" and first.audio_file_id == "fid-1"
        art = res[1]
        assert art.type == "article"
        assert art.thumbnail_url == "https://c.example/2.jpg"
        btns = art.reply_markup.inline_keyboard[0]
        assert btns[0].text == "⬇ Скачать"
        assert btns[0].url.endswith(f"?start=dl_{art.id[1:]}")
        assert btns[1].text == "📱 В приложении"

    async def test_card_text_escapes_html(self, fx_db):
        from core.models import Track
        async with fx_db() as s:
            s.add(Track(source="yandex", external_id="y:9",
                        title="Evil <b> & Co", artist="A"))
            await s.commit()
        async with fx_db() as s:
            tracks = await inline.db_search(s, "evil")
        text = inline._card_text(tracks[0])
        assert "Evil &lt;b&gt; &amp; Co" in text
        assert "<b> & Co" not in text


# ------------------------ фото-карточка батча ------------------------

class TestStartBatchCard:
    async def test_cover_photo_with_buttons(self, fx_db, monkeypatch):
        msg = _MsgStub()
        await start_batch(msg, "Плейлист", [_meta(1, cover="https://c/1.jpg"),
                                             _meta(2, cover="https://c/2.jpg")])
        assert len(msg.photos) == 1
        photo, kw = msg.photos[0]
        assert photo == "https://c/1.jpg"
        assert kw["reply_markup"] is not None
        assert "Треков: <b>2</b>" in kw["caption"]
        assert msg.texts == []
        # EXT_CACHE заполнен для кнопок батча
        assert msg.from_user.id in __import__("bot.batch", fromlist=["x"]).EXT_CACHE
        assert msg.bot.reactions == [(msg.chat.id, msg.message_id, [fx.FIRE])]

    async def test_no_cover_falls_back_to_text(self, fx_db):
        msg = _MsgStub()
        await start_batch(msg, "Список", [_meta(1)])
        assert msg.photos == []
        assert msg.texts and "Треков: <b>1</b>" in msg.texts[0][0]

    async def test_broken_cover_falls_back_to_text(self, fx_db):
        msg = _MsgStub()
        msg.fail_photo = True
        await start_batch(msg, "Список", [_meta(1, cover="https://broken/x.jpg")])
        assert msg.photos == []                      # попытка была
        assert msg.texts and "Треков: <b>1</b>" in msg.texts[0][0]


# --------------------------- диплинк dl_<id> ---------------------------

class TestDeepLink:
    async def test_dl_start_delivers_track(self, fx_db, monkeypatch):
        from core.models import Track
        async with fx_db() as s:
            tr = Track(source="yandex", external_id="y:7", title="T", artist="A")
            s.add(tr)
            await s.commit()
            tid = tr.id
        delivered: list[tuple] = []

        async def fake_deliver(bot, chat_id, s, track, **kw):
            delivered.append((chat_id, track.id))
            return True

        monkeypatch.setattr(runtime, "deliver_track", fake_deliver)
        msg = _MsgStub()
        msg.bot = _BotStub()
        status = _StatusStub()
        async def _answer(text, **kw):
            return status
        msg.answer = _answer

        await start_handler.cmd_start(msg, SimpleNamespace(args=f"dl_{tid}"))
        await asyncio.sleep(0.1)
        assert delivered == [(msg.chat.id, tid)]
        assert status.deleted

    async def test_bad_dl_arg_answers(self, fx_db):
        msg = _MsgStub()
        msg.bot = _BotStub()
        await start_handler.cmd_start(msg, SimpleNamespace(args="dl_x"))
        assert any("устарела" in t for t, _ in msg.texts)
