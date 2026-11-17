# OSS-компоненты и заимствованные идеи

Реестр по правилам мастер-промпта §1: что используется, на какой лицензии,
какие принципы адаптированы. Обновляется при каждом новом заимствовании.

| Проект | URL | Что взято | Файлы/места | Версия | Лицензия | Уведомления |
|---|---|---|---|---|---|---|
| Dexie.js | https://github.com/dexie/Dexie.js | зависимость: локальная БД (§3) | `frontend/src/local/db.js` (обёртка над Dexie) | 4.x (package.json) | Apache-2.0 | LICENSE в node_modules; переносится в дистрибуцию по требованию Apache-2.0 §4 |
| Snae Player (local-music-pwa) | https://github.com/minht11/local-music-pwa | принцип: local-first PWA-плеер без сервера, плейлисты из локальных файлов | только концепции; код не копировался | main @ 2026-11 | MIT | не требуется (код не заимствован) |
| Crate | https://github.com/aashir-athar/crate | принцип: менеджер загрузок с очередью, отмена/ретрай (§5) | `frontend/src/local/downloads.js` — собственная реализация | main @ 2026-11 | MIT | не требуется (код не заимствован) |
| Finamp | https://github.com/finamp-app/finamp | принципы: офлайн-события прослушивания с последующей синхронизацией (§17), gapless best-effort (§8) | концепции: `local/repo.js` (offline_events), `player.js` (предзагрузка следующего — этап P2+) | redesign @ 2026-11 | MPL-2.0 | если когда-нибудь заимствуем файлы — заголовки MPL-2.0 обязательны в изменённых файлах; пока код не заимствован |
| NaviPlayer Pro | https://github.com/MonseourM/NaviPlayer-Pro | принцип: waveform peaks (§12, этап P3) | — | main @ 2026-11 | MIT | не требуется |
| Local Music Player (kirillsaidov) | https://github.com/kirillsaidov/music-player | принцип: минимальный локальный плеер | только концепции | main @ 2026-11 | MIT | не требуется |
| Workbox | https://github.com/GoogleChrome/workbox | планируется: Service Worker app shell (§18, этап P3) | — | v7 | MIT | добавим при интеграции |
| WaveSurfer.js | https://github.com/katspaugh/wavesurfer.js | планируется: waveform (§12, этап P3) | — | 7.x | BSD-3-Clause | добавим при интеграции |
| music-metadata | https://github.com/Borewit/music-metadata | под вопросом: чтение тегов локальных файлов (P2+) | — | 11.x | MIT | добавим при интеграции |
| tma.js | https://github.com/Telegram-Mini-Apps/tma.js | под вопросом: SDK Telegram Mini Apps (сейчас используется нативный SDK Telegram) | — | master @ 2026-11 | MIT | при интеграции |

## Недоступные репозитории (код НЕ использовался)

- **EdgeDeck** — `https://github.com/j-un/edgedeck` → HTTP 404 (проверено GitHub API 2026-11-17).
- **Vibe Music** — `https://github.com/pseudocc/vibe-music` → HTTP 404 (там же).

Оба упоминались в мастер-промпте как ориентиры (hash-движок EdgeDeck §6).
Поскольку LICENSE невозможно проверить, **их код не используется вообще**.
SHA-256-хеширование написано с нуля по тексту стандарта FIPS 180-4:
`frontend/src/local/sha256.js` (+ `hash.worker.js`, `hash.js`).

## Правила этого реестра

1. Перед заимствованием кода проверяем LICENSE конкретной ветки/версии.
2. MPL-2.0 (Finamp): заимствованные файлы остаются MPL-2.0, изменения отмечаем
   заголовком файла; крупные заимствования — только по явному решению.
3. MIT/Apache/BSD: сохраняем copyright-уведомления там, где переносим текст лицензии.
4. «Концепции» = идея/архитектура; чистая-room реализация без копирования кода.
5. Уведомления третьих сторон, попавших в прод-бандл, собираются в этом файле.
