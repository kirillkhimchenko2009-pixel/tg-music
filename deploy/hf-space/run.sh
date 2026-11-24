#!/bin/bash
# Старт: восстановить базу из бэкапа → бэкап-вотчер → бот (под супервизором) → сервер.
set -e
cd /app
# Жёсткий потолок на restore: hf_hub_download внутри ретраит с бэкоффом и
# без таймаута может висеть минутами — контейнер тогда не проходит
# health-check и Render крутит бут по кругу («Application loading»). Bash
# timeout 120 убивает зависший restore: бот/сервер стартуют на пустой базе,
# бэкап-вотчер позже снова зальёт текущую базу.
timeout 120 python deploy/hf-space/backup.py restore || true
# вотчер держим только когда бэкап реально настроен: спящий python
# без дела — лишние ~40 МБ, на free-инстансе (512 МБ) это не мелочь.
if [ -n "$BACKUP_REPO" ] && [ -n "$HF_TOKEN" ]; then
  python deploy/hf-space/backup.py watch &
fi

# БОТ УБРАН с Render: поллинг переехал на HF Space (vibe-worker) — на
# free-инстансе 512 МБ сервер+бот+тяжёлые скачивания давали OOM-kill.
# Если нужно вернуть бота — раскомментируй блок ниже и запушь.
# (
#   set +e
#   while true; do
#     python run_bot.py
#     code=$?
#     echo "[run.sh] бот завершился (code=$code), перезапуск через 10 с"
#     sleep 10
#   done
# ) &

exec python run_server.py
