# -*- powershell -*-
# Supervisor for the Cloudflare quick tunnel: keeps cloudflared alive,
# extracts the fresh trycloudflare URL and writes it into data files
# (ASCII only, NO BOM - otherwise the bot watcher rejects the file).
$ErrorActionPreference = 'Continue'
$root = 'F:\TGMusicBot'
$exe  = Join-Path $root 'tools\cloudflared.exe'
$log  = Join-Path $root 'data\cloudflared.log'
$terr = Join-Path $root 'data\cloudflared.err'
$turl = Join-Path $root 'data\tunnel_url.txt'
$wurl = Join-Path $root 'data\webapp_url.txt'

while ($true) {
  Set-Content -Path $log -Value '' -Encoding ASCII -ErrorAction SilentlyContinue
  Set-Content -Path $terr -Value '' -Encoding ASCII -ErrorAction SilentlyContinue
  # http2: QUIC-датаграммы здесь режутся рутером/провайдером ("no recent network activity"),
  # http2-канал работает (с краткими флапами сразу после старта) и обслуживал трафик
  $p = Start-Process -FilePath $exe `
      -ArgumentList 'tunnel','--url','http://127.0.0.1:8000','--protocol','http2','--no-autoupdate','--ha-connections','4' `
      -RedirectStandardOutput $log -RedirectStandardError $terr `
      -PassThru -WindowStyle Hidden
  Write-Output ("cloudflared started pid=" + $p.Id + " at " + (Get-Date -Format 'HH:mm:ss'))

  $url = $null
  for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 2
    if ($p.HasExited) { break }
    $all = ''
    try { $all = (Get-Content $log -Raw -ErrorAction SilentlyContinue) + (Get-Content $terr -Raw -ErrorAction SilentlyContinue) } catch {}
    $m = [regex]::Match($all, 'https://[a-z0-9-]+\.trycloudflare\.com')
    if ($m.Success) { $url = $m.Value; break }
  }

  if ($url -and -not $p.HasExited) {
    Write-Output ("URL: " + $url + " - verifying...")
    $verified = $false
    for ($i = 0; $i -lt 10; $i++) {
      try {
        $r = Invoke-WebRequest -Uri ($url + '/api/ping') -Headers @{ 'X-Pinggy-No-Screen' = '1' } `
             -UserAgent 'vibe-probe/1.0' -UseBasicParsing -TimeoutSec 12
        if ($r.StatusCode -eq 200) { $verified = $true; break }
      } catch {}
      if ($p.HasExited) { break }
      Start-Sleep -Seconds 3
    }
    if ($verified -and -not $p.HasExited) {
      Write-Output ("VERIFIED: " + $url)
      [IO.File]::WriteAllText($turl, $url)
      [IO.File]::WriteAllText($wurl, $url + '/v2/')
      # health watchdog: edge 5xx continuously ~2 min -> kill and re-register (new URL)
      $fails = 0
      while (-not $p.HasExited) {
        Start-Sleep -Seconds 30
        $ok = $false
        try {
          $r = Invoke-WebRequest -Uri ($url + '/api/ping') -Headers @{ 'X-Pinggy-No-Screen' = '1' } `
               -UserAgent 'vibe-probe/1.0' -UseBasicParsing -TimeoutSec 12
          if ($r.StatusCode -eq 200) { $ok = $true }
        } catch {}
        if ($ok) { $fails = 0 } else { $fails++; Write-Output ("health fail " + $fails + " at " + (Get-Date -Format 'HH:mm:ss')) }
        if ($fails -ge 4) {
          Write-Output "tunnel unhealthy - killing for restart"
          Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
          break
        }
      }
    }
  }

  Write-Output ("cloudflared exited code=" + $p.ExitCode + " - restart in 5s")
  Start-Sleep -Seconds 5
}
