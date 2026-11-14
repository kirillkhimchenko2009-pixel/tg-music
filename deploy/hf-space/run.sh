#!/bin/bash
# Старт: восстановить базу из бэкапа → бэкап-вотчер → бот (под супервизором) → сервер.
set -e
cd /app
python deploy/hf-space/backup.py restore || true
# вотчер держим только когда бэкап реально настроен: спящий python
# без дела — лишние ~40 МБ, на free-инстансе (512 МБ) это не мелочь.
if [ -n "$BACKUP_REPO" ] && [ -n "$HF_TOKEN" ]; then
  python deploy/hf-space/backup.py watch &
fi

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
