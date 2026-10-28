# Автозапуск Tailscale-демона для мини-аппа (user-mode, dev pipe).
# Funnel-конфиг и логин хранятся в state (E:\Temp\opencode\tsstate) и
# восстанавливаются сами. Пайп: \\.\pipe\TailscaleBotPlain.
# Лог: F:\TGMusicBot\data\ts_daemon.log
$ErrorActionPreference = 'Continue'
$ts = 'C:\Program Files\Tailscale\tailscaled.exe'
$cli = 'C:\Program Files\Tailscale\tailscale.exe'
$pipe = '\\.\pipe\TailscaleBotPlain'
$state = 'E:\Temp\opencode\tsstate'
$log = 'F:\TGMusicBot\data\ts_daemon.log'

"BOOT $(Get-Date -Format o)" | Out-File $log -Append -Encoding utf8

function Daemon-Alive {
    $p = Get-CimInstance Win32_Process -Filter "Name='tailscaled.exe'" -ErrorAction SilentlyContinue
    return [bool]($p | Where-Object { $_.CommandLine -match 'TailscaleBotPlain' })
}

if (-not (Daemon-Alive)) {
    Start-Process -FilePath $ts -ArgumentList @(
        '-tun=userspace-networking', '-windows-mode=dev',
        '-socket', $pipe, '-statedir', $state, '-port', '41642'
    ) -WindowStyle Hidden
    Start-Sleep -Seconds 8
    "daemon started" | Out-File $log -Append -Encoding utf8
} else {
    "daemon already running" | Out-File $log -Append -Encoding utf8
}

# Определяет запущенный бэкенд (переживает сброс после разрыва CLI-сессий).
& $cli --socket $pipe up --unattended --accept-dns=$false --accept-routes=$false --hostname=tnmusicbot *>> $log
Start-Sleep -Seconds 5
& $cli --socket $pipe status *>> $log

# Убедиться, что funnel жив (перезьние в т-стейте; переоверкидаем на всяк).
& $cli --socket $pipe funnel --bg 8000 *>> $log
"BOOT done" | Out-File $log -Append -Encoding utf8
