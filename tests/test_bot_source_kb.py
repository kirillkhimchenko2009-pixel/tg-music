"""UI выбора источника в боте (мастер-промпт §5): кнопки областей после
результатов, URL-кнопки для OFFICIAL_LINK_ONLY, пагинация по областям."""
from __future__ import annotations

import types

from bot.keyboards import search_kb
from core.models import Track


def _track(id_: int, source: str, title: str = "Трек",
           page_url: str | None = None, file_id: str | None = None) -> Track:
    t = Track(source=source, external_id=f"x{id_}", title=title, artist="Артист")
    t.id = id_
    t.page_url = page_url
    t.file_id = file_id
    return t


class TestScopeButtons:
    def test_scope_row_present(self):
        kb = search_kb([_track(1, "zaycev")], page=0, has_more=False)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        datas = [b.callback_data for row in kb.inline_keyboard for b in row
                 if b.callback_data]
        assert any("🌐 Все" in lb for lb in labels)
        assert any("🇷🇺" in lb for lb in labels)
        assert any("🌍" in lb for lb in labels)
        assert any("📥" in lb for lb in labels)
        assert any("🔗" in lb for lb in labels)
        for scope in ("all", "ru", "intl", "downloadable", "official_links"):
            assert f"srcsc:{scope}:0" in datas

    def test_nav_and_track_buttons_kept(self):
        kb = search_kb([_track(1, "zaycev"), _track(2, "audius")],
                       page=2, has_more=True)
        datas = [b.callback_data for row in kb.inline_keyboard for b in row
                 if b.callback_data]
        assert "srch:1" in datas and "srch:3" in datas
        assert "dl:1" in datas and "dl:2" in datas

    def test_url_button_for_official_link_only(self):
        """Карточки VK/МТС/Lisen/FMA → URL-кнопка на официальную страницу."""
        t = _track(9, "vk", page_url="https://vk.com/audio?q=кино")
        kb = search_kb([t], page=0, has_more=False)
        url_btns = [b for row in kb.inline_keyboard for b in row if b.url]
        assert url_btns and url_btns[0].url == "https://vk.com/audio?q=кино"
        datas = [b.callback_data for row in kb.inline_keyboard for b in row
                 if b.callback_data]
        assert "dl:9" not in datas                        # качать нечего — нет кнопки

    def test_downloadable_source_keeps_dl_button(self):
        t = _track(1, "audius", page_url="https://audius.co/x/y")
        kb = search_kb([t], page=0, has_more=False)
        datas = [b.callback_data for row in kb.inline_keyboard for b in row
                 if b.callback_data]
        assert "dl:1" in datas

    def test_empty_results_still_show_scopes(self):
        kb = search_kb([], page=0, has_more=False)
        datas = [b.callback_data for row in kb.inline_keyboard for b in row
                 if b.callback_data]
        assert any(d.startswith("srcsc:") for d in datas)
