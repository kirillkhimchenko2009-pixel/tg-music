# Vibe — воркер скачиваний на Hugging Face Spaces (бесплатный CPU).
# Отдельный Space только для очереди: не трогает память Render и не зависит
# от Botkeep. Docker Spaces обязаны слушать порт 7860 — рядом с воркером
# поднимаем простенький health-сервер (его и пингует UptimeRobot).
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-worker.txt .
RUN pip install --no-cache-dir -r requirements-worker.txt || \
    pip install --no-cache-dir --break-system-packages -r requirements-worker.txt

COPY . .

# HF запускает контейнер от uid 1001 — права на запись данных
RUN useradd -m -u 1001 user && mkdir -p /app/data && chown -R user:user /app
USER user

ENV PYTHONUNBUFFERED=1 \
    MEDIA_ROLE=worker \
    MEDIA_WORKER_NAME=hf-worker \
    MEDIA_POLL_SEC=3

CMD ["/app/run.sh"]
