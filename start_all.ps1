# Быстрый локальный старт: сервер + бот в двух окнах PowerShell
# (запусти: powershell -ExecutionPolicy Bypass -File .\start_all.ps1)
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location F:\TGMusicBot; .\.venv\Scripts\python run_server.py"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location F:\TGMusicBot; .\.venv\Scripts\python run_bot.py"
Write-Output "Server: http://127.0.0.1:8000  ·  Бот запущен отдельным окном"
Write-Output "Mini App нужен https-адрес: cloudflared tunnel --url http://localhost:8000"
