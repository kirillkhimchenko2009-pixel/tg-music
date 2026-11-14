"""Мини-сервер только для /ympx: русский выход анонимного API Яндекс.Музыки.

Подаёт наружу единственный роут (server.routes.ympx) — доступ по ключу
X-Ympx-Key. Публичный адрес выдаёт туннель (nurl):
    nurl 8811
и вписывается на проде в env YMPX_BASE. Основной бот и Mini App живут
на Render — этот сервер только «провод» к русскому IP.
Запуск: python run_ympx.py   (порт 8811, или PORT=...)
"""
import os

import uvicorn

from server.routes.ympx import router


def build_app() -> "FastAPI":
    from fastapi import FastAPI

    app = FastAPI(title="ympx", docs_url=None, redoc_url=None)
    app.include_router(router)
    return app


app = build_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8811"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
