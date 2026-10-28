#!/bin/bash
# Старт: восстановить базу из бэкапа → бэкап-вотчер → бот (под супервизором) → сервер.
set -e
cd /app
python deploy/hf-space/backup.py restore || true
python deploy/hf-space/backup.py watch &

# Бот под супервизором: если он упадёт (сбой сети, неверный токен и т.п.) —
# перезапускаем через 10 с. Сервер при этом продолжает работать и
# health-check остаётся зелёным.
(
  set +e
  while true; do
    python run_bot.py
    code=$?
    echo "[run.sh] бот завершился (code=$code), перезапуск через 10 с"
    sleep 10
  done
) &

exec python run_server.py
