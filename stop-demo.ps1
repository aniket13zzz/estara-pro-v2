# Stop the demo: the background tunnel and ESTARA's containers (data is kept).
#   powershell -ExecutionPolicy Bypass -File .\stop-demo.ps1
Set-Location -Path $PSScriptRoot
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
docker compose stop
Write-Host "Stopped. Run .\start-demo.ps1 for the next demo." -ForegroundColor Green
