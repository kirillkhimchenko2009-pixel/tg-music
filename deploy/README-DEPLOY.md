# Деплой Vibe Music — постоянно и бесплатно

> Состояние на октябрь 2026. Лимиты бесплатных тарифов провайдеры меняют —
> перед регистрацией сверься с официальной страницей тарифа.

## ВЫБРАНО: Render (free) + Neon Postgres — без карты, 24/7

Факты, из-за которых так:
- **Hugging Face Spaces**: статические Space бесплатны, но Gradio/Docker
  теперь требуют PRO-подписку (подтверждено документацией HF и 402-ошибкой).
- **Render free**: веб-сервис 24/7 в пределах 750 ч/мес; засыпает после
  15 минут без трафика — лечится бесплатным пингером раз в 10 минут.
- **Neon free**: Postgres 0.5 ГБ, автоприостановка после 5 минут простоя
  (просыпается за ~1 с на первом же запросе), без карты.

Бот и сервер едут **одним контейнером** (CMD Dockerfile запускает оба —
`deploy/hf-space/run.sh`), потому что «фоновые воркеры» Render платные.

### Шаги

1. **Neon** (база): neon.tech → регистрация по email → New Project →
   скопировать Pooled connection string (`postgresql://...neon.tech/neondb?sslmode=require`)
   → передать мне: я прогоню перенос базы `tools/migrate_sqlite_to_pg.py`.
2. **GitHub** (исходники): аккаунт → новый **приватный** репозиторий
   (например `vibe-music`) → токен (Settings → Developer settings → Fine-grained,
   права Contents: Read+Write на этот репо) → передать мне: я запушу код
   (без `.env`, аудио-кэша и базы — они не в репо по .gitignore).
3. **Render**: render.com → Sign in **with GitHub** → New → Blueprint →
   выбрать репозиторий: `render.yaml` в корне сам создаст сервис
   `vibe-music` (Docker, health-check `/api/ping`).
   В окне создания введи секреты, которые yaml пометил `sync: false`:
   - `DATABASE_URL` — строка подключения из шага 1;
   - `BOT_TOKEN` — токен бота (есть в твоём локальном `.env`).
4. **Пингер**: cron-job.org (бесплатно) → задание: GET
   `https://vibe-music.onrender.com/api/ping` каждые 10 минут —
   не даёт сервису заснуть.
5. Проверка: `/api/ping` → 200; `https://vibe-music.onrender.com/v2/` открывается;
   плейлисты на месте (Postgres), треки играют (реле Telegram CDN работает отовсюду).
   Бот при старте сам поставит меню-кнопку на новый адрес (`WEBAPP_URL` в yaml).

### Обновления кода
`git push` в GitHub → Render пересобирает образ автоматически (autoDeploy: true).

### Если имя `vibe-music.onrender.com` занято
Переименуй сервис в render.yaml/на дашборде (адрес = `<имя>.onrender.com`)
и поправь `WEBAPP_URL` в render.yaml.

## Маршрут «в обход GitHub» (когда аккаунт GitHub под флагом — OAuth чужим приложениям закрыт)

1. Образ собирается локально и выкатывается в Docker Hub:
   ```powershell
   docker build -t vibe-music:local .
   docker tag vibe-music:local <hub-user>/vibe-music:latest
   docker push <hub-user>/vibe-music:latest
   ```
2. Render: регистрация **по email** (не GitHub) → New → Web Service →
   вкладка **Existing image** → `docker.io/<hub-user>/vibe-music:latest`
   → Free plan, регион Frankfurt → Environment:
   - `DATABASE_URL` — строка Neon;
   - `BOT_TOKEN` — токен бота;
   - `WEBAPP_URL` — `https://<имя-сервиса>.onrender.com/v2/` (посмотреть фактический адрес в шапке сервиса после создания);
   - Health Check Path: `/api/ping`.
3. Если Render не может вытащить образ — сделай репозиторий в Docker Hub
   публичным (Details → Visibility → Public; в образе нет ни секретов, ни данных).
4. Пингер (cron-job.org) на `https://<адрес>/api/ping` каждые 10 минут.

## Запасной путь: Koyeb free

Тот же контейнер без сна и без холодных стартов (512 МБ RAM):
koyeb.com → Create Service → из GitHub-репо или Docker-образа →
env-переменные: `DATABASE_URL`, `BOT_TOKEN`, `WEBAPP_URL=https://<app>.koyeb.app/v2/`.
Free-инстанс один; если просит карту — остаёмся на Render.

## Апгрейд, когда захочешь мощнее: Oracle Cloud Always Free

Вечная VM (ARM до 4 vCPU / 24 ГБ RAM, свой IP, постоянный диск, YouTube без
прокси). Нужна карта для верификации (не списывают; российские часто не проходят).
Готовый стек: `deploy/docker-compose.vps.yml` + `deploy/Caddyfile` (авто-HTTPS).
Переезд = `docker compose -f deploy/docker-compose.vps.yml up -d --build` + перенос
`music.db` тем же скриптом миграции (Postgres можно поднять прямо в VM).

## Что где живёт после переезда

| Что | Где | Переживает рестарт? |
|---|---|---|
| Каталог, плейлисты, юзеры | Neon Postgres | да |
| Скачанные файлы в чатах | Telegram | да |
| Локальный аудио-кэш (`data/audio`) | диск контейнера (эфемерный) | нет — но он лишь кэш: играет реле Telegram |
| Кэш обложек | диск контейнера | нет — берутся заново по URL |
| Стрим-токены, auth-коды | Postgres / память | ок |

## Перенос базы (выполняю я, после твоего DATABASE_URL)

```powershell
$env:DATABASE_URL = "<строка Neon>"
.\.venv\Scripts\python.exe tools\migrate_sqlite_to_pg.py
```
Скрипт делает согласованный снапшот `data/music.db`, создаёт таблицы,
переносит строки, продвигает счётчики. Повторный запуск безопасен.
