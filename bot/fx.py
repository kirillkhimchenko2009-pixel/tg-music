"""Мелкие Telegram-виджеты: реакции-эмодзи и индикаторы действий.

Реакции: бот отмечает сообщения, чтобы фидбек был мгновенным и без
лишнего текста — 👍 файл доставлен, 🔥 список/плейлист принят,
❤ добавлено в «Любимое», 😢 не получилось. В приватных чатах бот может
реагировать на любые сообщения; в группах — только на свои, поэтому
любая ошибка реакции глушится (виджет не должен ломать флоу).
"""
import asyncio
import html
import logging

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.types import ReactionTypeEmoji

log = logging.getLogger("bot.fx")


def esc_html(s: str | None) -> str:
    """Экранирование пользовательских данных в HTML-подписях/карточках."""
    return html.escape(s or "", quote=False)

# Только белый список Telegram (custom emoji ботам без премии недоступны)
OK = "👍"        # файл доставлен в чат
FIRE = "🔥"      # список/плейлист принят
HEART = "❤"      # ушло в «Любимое»
SAD = "😢"       # ошибки в пачке


async def react(bot: Bot, chat_id: int, message_id: int | None,
                emoji: str) -> None:
    """Одна эмодзи-реакция на сообщение; любые проблемы — молча."""
    if not message_id:
        return
    try:
        await bot.set_message_reaction(
            chat_id=chat_id, message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)])
    except Exception as e:  # noqa: BLE001
        log.debug("reaction %s on %s/%s пропущена: %s",
                  emoji, chat_id, message_id, e)


def typing(chat_id: int, bot: Bot,
           action: str = ChatAction.TYPING) -> asyncio.Task:
    """«Печатает…» / «загружает файл…» — fire-and-forget на ~5 секунд."""
    async def _run() -> None:
        try:
            await bot.send_chat_action(chat_id, action)
        except Exception:  # noqa: BLE001
            pass
    return asyncio.create_task(_run())
