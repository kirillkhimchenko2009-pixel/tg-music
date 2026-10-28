FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# HF Spaces запускает контейнер от uid 1001 — права на запись данных
RUN useradd -m -u 1001 user && mkdir -p /app/data && chown -R user:user /app
USER user

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=7860

EXPOSE 7860

# Один контейнер: бот (polling) + сервер мини-аппа с бэкап-вотчером базы
CMD ["/app/deploy/hf-space/run.sh"]
