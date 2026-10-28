#!/bin/bash
# Старт HF Space: восстановить базу из бэкапа → бэкап-вотчер → сервер + бот.
set -e
cd /app
python deploy/hf-space/backup.py restore || true
python deploy/hf-space/backup.py watch &
python run_bot.py &
exec python run_server.py
