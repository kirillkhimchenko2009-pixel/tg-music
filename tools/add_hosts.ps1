Add-Content -Path "C:\Windows\System32\drivers\etc\hosts" -Value "127.0.0.1 argotunnel.com" -Encoding ASCII
Add-Content -Path "C:\Windows\System32\drivers\etc\hosts" -Value "127.0.0.1 v2-argotunnel.com" -Encoding ASCII
Write-Output "hosts updated"
