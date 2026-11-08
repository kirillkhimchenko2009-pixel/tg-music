"""Живой прогресс загрузки в Telegram.

Редактируем одно сообщение, но не чаще `min_interval` секунд: иначе
Telegram отвечает 429 и пользователь видит «мигание». Ошибки редактирования
не должны ломать саму загрузку.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

log = logging.getLogger("music.telegram.progress")

_STEP_LABELS = {
    "queued": "В очереди",
    "permission": "Проверка разрешения",
    "cache": "Проверка кэша",
    "download": "Загрузка файла",
    "analyze": "Анализ качества",
    "cover": "Проверка обложки",
    "metadata": "Подготовка метаданных",
    "done": "Готово",
}

_BAR_CELLS = 10


def progress_bar(percent: int) -> str:
    value = max(0, min(100, int(percent)))
    filled = round(value / 100 * _BAR_CELLS)
    return "█" * filled + "░" * (_BAR_CELLS - filled)


def _label(step: str) -> str:
    return _STEP_LABELS.get(step, step or "Обработка")


class ProgressView:
    """Обёртка над сообщением прогресса с троттлингом и безопасным edit."""

    def __init__(self, message: types.Message, *, title: str = "",
                 min_interval: float = 2.2):
        self.message = message
        self.title = title
        self.min_interval = max(0.0, float(min_interval))
        self._last_update = 0.0
        self._last_text = ""
        self._percent = 0
        self._lock = asyncio.Lock()

    def _render(self, step: str, percent: int, detail: str = "") -> str:
        percent = max(self._percent, min(100, int(percent)))
        self._percent = percent
        lines = []
        if self.title:
            lines.append(f"🎧 <b>{html.escape(self.title[:160])}</b>")
        lines.append(f"<code>{progress_bar(percent)}</code> {percent}%")
        label = _label(step)
        text = f"{label}…" if percent < 100 else label
        if detail:
            text += f" — {html.escape(detail[:200])}"
        lines.append(text)
        return "\n".join(lines)

    async def update(self, step: str, percent: int, detail: str = "",
                     *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_update < self.min_interval and 0 < percent < 100:
            return
        text = self._render(step, percent, detail)
        if text == self._last_text:
            return
        async with self._lock:
            if text == self._last_text:
                return
            self._last_text = text
            self._last_update = now
            try:
                await self.message.edit_text(text, parse_mode="HTML")
            except TelegramRetryAfter as exc:
                # мягко ждём и больше не спамим
                await asyncio.sleep(min(5, int(exc.retry_after or 1)))
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    log.debug("progress edit: %s", exc)
            except Exception as exc:  # noqa: BLE001
                log.debug("progress edit: %s", exc)

    async def finish(self, text: str, reply_markup=None) -> None:
        try:
            await self.message.edit_text(text, parse_mode="HTML",
                                         reply_markup=reply_markup,
                                         disable_web_page_preview=True)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                log.debug("progress finish: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.debug("progress finish: %s", exc)


async def send_progress(message: types.Message, text: str) -> ProgressView:
    msg = await message.answer(text, parse_mode="HTML",
                               disable_web_page_preview=True)
    return ProgressView(msg)


__all__ = ["ProgressView", "progress_bar", "send_progress"]
