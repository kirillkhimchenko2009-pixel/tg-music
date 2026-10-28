"""Запуск сервера Mini App: python run_server.py"""
import os

import uvicorn

from core.single_instance import acquire

if __name__ == "__main__":
    acquire("server")
    # HOST/PORT из окружения: локально 127.0.0.1:8000, в контейнере/облаке 0.0.0.0:7860 и т.п.
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("server.main:app", host=host, port=port, reload=False)
