"""Запуск бота: python run_bot.py"""
import asyncio

from core.single_instance import acquire

if __name__ == "__main__":
    acquire("bot")
    from bot.main import run
    asyncio.run(run())