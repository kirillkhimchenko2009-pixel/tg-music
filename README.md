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
- 🎼 **Музыкальный хаб `/music`**: легальный поиск по Jamendo, Internet Archive,
  SoundCloud, Spotify, Bandcamp и своим файлам; проверка прав, качества,
  обложки и метаданных перед отправкой (подробнее ниже)

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

5. Mini App уже задеплоен вместе с сервером: `https://<адрес-сервера>/v2/`.
   Для локального ympx-сервера (русский выход для Яндекс.Музыки) наружу
   его открывает туннель nurl (`nurl 8811`), URL идёт на прод в `YMPX_BASE`.

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
| SPOTIFY_CLIENT_ID / SECRET | client credentials для чтения публичных плейлистов Spotify и метаданных музыки |
| JAMENDO_CLIENT_ID | бесплатный client_id Jamendo (не обязательно; без него Jamendo выключен) |
| SOUNDCLOUD_CLIENT_ID / SECRET | официальный API SoundCloud (не обязательно; только треки с официальной загрузкой) |
| MUSIC_ENABLED | `1` включает `/music`, кнопку «Музыка» и очередь (по умолчанию включено) |
| MUSIC_AUTO_TEXT | `1` — обычный текст в боте идёт в музыкальный поиск; по умолчанию `0` |

## Музыкальный хаб `/music`

Отдельная подсистема в пакете `music/` работает параллельно с legacy-поиском и
не меняет существующие сценарии. Вход:

- команда `/music` (можно сразу с запросом: `/music Daft Punk — One More Time`);
- кнопка **🎵 Музыка** в reply-клавиатуре;
- inline-хаб: поиск, ссылка, свой файл, избранное, история, настройки.

Что делает подсистема:

1. **Поиск по нескольким источникам** — Jamendo, Internet Archive, SoundCloud,
   Spotify, Bandcamp. Провайдеры независимы: ошибка одного не ломает остальные.
2. **Нормализация и дедупликация** — исполнитель/название, remix/live/acoustic,
   ISRC, длительность; альтернативные источники показываются в карточке.
3. **Permission Checker** — скачивание только при `DOWNLOAD_ALLOWED`. Иначе
   выдаётся официальная ссылка (`STREAM_ONLY`, `OFFICIAL_LINK_ONLY`,
   `AUTH_REQUIRED`, `PURCHASE_REQUIRED`, `LICENSE_UNKNOWN`, `BLOCKED`).
   DRM, подписки, гео-ограничения и cookies не обходятся.
4. **Audio Quality Engine** — FFprobe/FFmpeg: кодек, битрейт, частота, клиппинг,
   тишина, повреждения, признаки перекодирования. Без лишнего перекодирования.
5. **Cover Integrity** — оригинал максимального разрешения, проверка чёткости и
   повреждений, без апскейла; размытая копия — только для фона.
6. **Metadata Engine** — Mutagen: title, artist, album, album artist, track/disc,
   year, genre, composer, ISRC, встроенная обложка.
7. **Отправка** — MP3/M4A через `sendAudio`, FLAC/WAV/OGG/OPUS через
   `sendDocument` (документом, без конвертации). Красивый caption и кэш file_id.
8. **Очередь** — уникальные job id, отмена, ретраи, таймауты, лимиты на
   пользователя, очистка временных файлов.

### Что нужно для источников

| Источник | Что доступно | Условие |
|---|---|---|
| Jamendo | поиск и скачивание | `JAMENDO_CLIENT_ID` + `audiodownload_allowed` |
| Internet Archive | поиск и скачивание | только явная CC/PD-лицензия |
| SoundCloud | поиск и скачивание | официальный API + трек с официальной загрузкой |
| Spotify | метаданные и официальная ссылка | `SPOTIFY_CLIENT_ID/SECRET`; файлы не скачиваются |
| Bandcamp | метаданные и официальная страница | прямая ссылка; покупка не обходится |
| Свой файл | полный пайплайн | файл загружен пользователем |

Без ключей соответствующие адаптеры просто выключены (`unconfigured`) и не
мешают работе остальных.

### API и диагностика

```bash
GET /api/music/health          # БД, FFmpeg, диск, очередь, провайдеры
GET /api/music/health?deep=1   # плюс ping внешних API (медленно)
GET /api/music/sources         # статус и возможности источников
GET /api/music/stats           # треки, загрузки, размер кэша
```

### Тесты

```bash
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest
```

Тесты используют синтетические WAV/MP3/M4A/FLAC/OGG/OPUS, изолированную
in-memory SQLite и временный каталог — реальная БД и файлы не затрагиваются.
Для аудиотестов нужен установленный FFmpeg.

## Запуск на VPS

1. Установить Python 3.11+ и FFmpeg/FFprobe:
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv ffmpeg
   ffmpeg -version && ffprobe -version
   ```
2. Скопировать проект и создать окружение:
   ```bash
   cd /opt/TGMusicBot
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Заполнить `.env` (минимум `BOT_TOKEN`, `WEBAPP_URL`, `ADMIN_IDS`; при
   необходимости — `DATABASE_URL` для Postgres и ключи музыкальных источников).
4. Создать каталоги и выдать права пользователю сервиса:
   ```bash
   mkdir -p data/music/tmp data/music/cache data/music/covers data/music/quarantine
   sudo chown -R www-data:www-data /opt/TGMusicBot/data
   ```
5. Проверить запуск вручную:
   ```bash
   python run_server.py     # FastAPI + Mini App
   python run_bot.py        # Telegram-бот (второе окно/сервис)
   ```
6. Для автозапуска — два systemd-юнита (пример):
   ```ini
   # /etc/systemd/system/tgmusic-server.service
   [Unit]
   Description=TGMusicBot API
   After=network.target
   [Service]
   WorkingDirectory=/opt/TGMusicBot
   EnvironmentFile=/opt/TGMusicBot/.env
   ExecStart=/opt/TGMusicBot/.venv/bin/python run_server.py
   Restart=always
   User=www-data
   [Install]
   WantedBy=multi-user.target
   ```
   ```ini
   # /etc/systemd/system/tgmusic-bot.service
   [Unit]
   Description=TGMusicBot Telegram bot
   After=network.target
   [Service]
   WorkingDirectory=/opt/TGMusicBot
   EnvironmentFile=/opt/TGMusicBot/.env
   ExecStart=/opt/TGMusicBot/.venv/bin/python run_bot.py
   Restart=always
   User=www-data
   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now tgmusic-server tgmusic-bot
   sudo journalctl -u tgmusic-bot -f
   ```
7. После обновления кода:
   ```bash
   cd /opt/TGMusicBot
   . .venv/bin/activate
   pip install -r requirements.txt
   sudo systemctl restart tgmusic-server tgmusic-bot
   ```

Если используется Docker (`docker compose up -d`), достаточно пробросить `.env`
и volume для `data/`; FFmpeg уже есть в образе. Музыкальные таблицы создаются
автоматически при старте бота/сервера (`init_music_db()`), отдельная миграция
не требуется.

## Структура
```
core/      — конфиг, БД, модели (SQLAlchemy)
services/  — провайдеры поиска/скачивания (YouTube, ЯМ, Звук, Spotify), конвертер, очередь
music/     — легальная музыкальная подсистема: провайдеры, поиск, качество, метаданные, очередь, Telegram-хаб
bot/       — aiogram 3: хендлеры, клавиатуры, отправка
server/    — FastAPI: REST API для Mini App + статику
webapp/    — Mini App (vanilla SPA, без сборки)
data/      — runtime: БД, кэш, обложки, временные файлы
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
