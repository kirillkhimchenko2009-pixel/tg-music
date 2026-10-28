---
title: Vibe Music
emoji: 🎧
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# TGMusicBot — аналог GB_music_bot

Telegram-бот + Mini App для поиска, скачивания и хранения треков, переноса плейлистов
(Яндекс Музыка, Spotify, Звук) и управления собственными плейлистами.

## Возможности
- 🔍 Поиск по запросу → inline-кнопки с обложкой → скачивание в ТГ
- ➕ Добавление трека текстом (`/add Название — Исполнитель`)
- 📋 Массовая загрузка списком (txt-файл или сообщение списком)
- 🔗 Обработка ссылок на плейлисты (Яндекс Музыка / Spotify / Звук / YouTube)
- 🎵 Плейлисты: создание в боте и в мини-приложении (единая база)
- 💾 Каталог треков: автонаполнение, кеш file_id — повторная отправка мгновенная
- 🎧 Качество: FLAC (lossless, когда источник даёт) → fallback AAC 320 kbps
- 📱 Mini App: плеер, поиск, моя музыка, плейлисты (тёмная малиновая тема)

## Быстрый старт (Windows, локально)

1. Скопировать `.env.example` → `.env` и заполнить
   `BOT_TOKEN` (у @BotFather), `WEBAPP_URL` (домен/туннель https).

2. Создать venv и установить зависимости:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

3. Запустить сервер мини-приложения (порт 8000):
   ```powershell
   .\.venv\Scripts\python run_server.py
   ```

4. Запустить бота (в отдельном окне):
   ```powershell
   .\.venv\Scripts\python run_bot.py
   ```

5. Для Mini App Telegram требует https: пробросить туннель
   ```powershell
   cloudflared tunnel --url http://localhost:8000
   ```
   полученный URL указать в `.env` как `WEBAPP_URL`, в чате открыть бота → кнопка «Приложение».

## Docker
```bash
docker compose up -d    # сервер + бот + локальный Bot API (лимит файлов 2 ГБ для FLAC)
```

## Что нужно в .env
| Переменная | Зачем |
|---|---|
| BOT_TOKEN | токен бота от @BotFather |
| WEBAPP_URL | https-адрес мини-приложения |
| ADMIN_IDS | id админов ( тех поддержка через @getmyid_bot) |
| YM_TOKEN | токен Яндекс Музыки (не обязательно; включит поиск/скачивание ЯМ) |
| ZVUK_TOKEN | токен Звука (не обязательно; включит FLAC от Звука) |
| SPOTIFY_CLIENT_ID / SECRET | client credentials для чтения публичных плейлистов Spotify |

## Структура
```
core/      — конфиг, БД, модели (SQLAlchemy)
services/  — провайдеры поиска/скачивания (YouTube, ЯМ, Звук, Spotify), конвертер, очередь
bot/       — aiogram 3: хендлеры, клавиатуры, отправка
server/    — FastAPI: REST API для Mini App + статику
webapp/    — Mini App (vanilla SPA, без сборки)
data/      — runtime: БД, качевания, обложки
```

## Сеть и источники
| Источник | Что даёт | Что нужно |
|---|---|---|
| YouTube Music | поиск + скачивание | доступ к youtube.com. Из РФ нужен `PROXY_URL=http://host:port` в .env (поддержу и SOCKS) |
| Яндекс Музыка | поиск, 320 kbps, FLAC по Plus, приватные плейлисты | токен: `/token` в боте (или YM_TOKEN в .env) |
| Звук | поиск, FLAC стрим | токен: `/token` в боте (или ZVUK_TOKEN в .env) |
| Spotify | метаданные публичных плейлистов | SPOTIFY_CLIENT_ID/SECRET + доступ к api.spotify.com (иногда тоже через PROXY_URL) |

Качество выбирается цепочкой: FLAC (Звук/ЯМ Plus) → AAC 320 → лучший доступный вариант.
Для отправки FLAC > 50 МБ боту нужен локальный Bot API сервер (см. docker-compose.yml, сервис botapi):
поставь `api_id/api_hash` с my.telegram.org, раскомментируй сервис и укажи `BOT_API_URL=http://...`.

⚠️ Из этого окна: с локальной машины без прокси сеть youtube/spotify может блокироваться —
бот честно сообщит «нет доступных источников» и подскажет, что настроить.

### Не работает? Быстрая диагностика
```powershell
# достижимость источников
curl.exe -I https://music.yandex.ru
curl.exe -I --proxy http://127.0.0.1:1080 https://music.youtube.com
```

⚠️ Скачивание лицензионной музыки — для личного пользования.
