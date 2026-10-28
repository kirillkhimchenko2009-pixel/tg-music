# Vibe Music — один контейнер: сервер (мини-апп + API) и бот.
# Для Hugging Face Spaces (бесплатный CPU): публичный HTTPS на 7860.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# HF запускает контейнер от uid 1001 — даём права на запись данных
RUN useradd -m -u 1001 user && mkdir -p /app/data && chown -R user:user /app
USER user

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=7860

# restore-on-boot + циклический бэкап базы; затем сервер+бот
CMD ["/app/deploy/hf-space/run.sh"]
