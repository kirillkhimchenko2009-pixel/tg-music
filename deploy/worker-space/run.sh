#!/bin/bash
# Старт воркера в HF Space: health на 7860 (HF проверяет порт) + воркер.
set -e
cd /app

# Простейший HTTP health: HF требует живой порт, UptimeRobot держит Space
# проснувшимся (free Space засыпает после 48ч без HTTP-трафика).
python -c "
import http.server, threading
h = http.server.BaseHTTPRequestHandler
class H(h):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b'{\"ok\":true,\"worker\":\"hf\"}')
    def log_message(self, *a): pass
srv = http.server.HTTPServer(('0.0.0.0', int(__import__('os').environ.get('PORT', 7860))), H)
srv.serve_forever()
" &
HEALTH_PID=$!

# Воркер сам ставит недостающие pip-пакеты (bootstrap в run_worker.py)
python run_worker.py &
WORKER_PID=$!

# Если умер воркер — умирает и health, HF перезапустит контейнер
wait $WORKER_PID || kill $HEALTH_PID 2>/dev/null || true
