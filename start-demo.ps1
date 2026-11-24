# Make ESTARA ready for a demo, in one go (Windows PowerShell 5.1 or 7):
#   powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
# 1. starts ESTARA in Docker, 2. starts a Cloudflare tunnel in the background,
# 3. writes its address into .env (PUBLIC_BASE_URL), 4. restarts ESTARA with it,
# 5. points the Vapi assistants at it (A if VAPI_ASSISTANT_ID is set, B if VAPI_ASSISTANT_ID_B is
#    set), 6. checks ESTARA answers through the tunnel, 7. runs the demo check (doctor).
#    Stop with .\stop-demo.ps1.
# It never prints or changes any key in .env except PUBLIC_BASE_URL.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$site = "https://estara-pro-public.vercel.app"
$envFile = Join-Path $PSScriptRoot ".env"
$log = Join-Path $env:TEMP "estara-tunnel.log"

function Step($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "STOPPED: $text" -ForegroundColor Red; exit 1 }
function EnvValue($name) {
    $line = Get-Content $envFile | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if ($line) { return ($line -replace "^$name=", "").Trim() }
    return ""
}

if (-not (Test-Path $envFile)) { Fail ".env not found in $PSScriptRoot" }
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Fail "cloudflared is not installed (or open a new PowerShell window after installing it)."
}

Step "1/6 Starting ESTARA (Docker)..."
docker compose up -d --wait
if ($LASTEXITCODE -ne 0) { Fail "Docker did not start. Open Docker Desktop, wait until it is running, and try again." }

Step "2/6 Starting the tunnel in the background..."
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item $log -ErrorAction SilentlyContinue
Start-Process -FilePath "cloudflared" -WindowStyle Hidden `
    -ArgumentList @("tunnel", "--url", "http://localhost:8000", "--logfile", $log)
$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
        $found = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" |
            Select-Object -First 1
        if ($found) { $url = $found.Matches[0].Value }
    }
}
if (-not $url) { Fail "The tunnel gave no address within 60 seconds. Details: $log" }
Write-Host "Tunnel address: $url"

Step "3/6 Writing the address into .env..."
$lines = @(Get-Content $envFile)
if ($lines -match "^PUBLIC_BASE_URL=") {
    $lines = $lines -replace "^PUBLIC_BASE_URL=.*", "PUBLIC_BASE_URL=$url"
} else {
    $lines += "PUBLIC_BASE_URL=$url"
}
# UTF-8 without a byte-order mark, so Docker reads the first line correctly.
[System.IO.File]::WriteAllLines($envFile, [string[]]$lines)

Step "4/6 Restarting ESTARA with the new address..."
docker compose up -d --force-recreate --wait
if ($LASTEXITCODE -ne 0) { Fail "ESTARA did not restart. Run: docker compose logs backend --tail 50" }

Step "5/6 Pointing Vapi at the new address..."
$updated = 0
if (EnvValue "VAPI_ASSISTANT_ID_B") {
    docker compose run --rm --no-deps backend python -m app.channels.vapi.api_tools
    if ($LASTEXITCODE -ne 0) { Fail "Updating assistant B failed (message above)." }
    $updated++
}
if (EnvValue "VAPI_ASSISTANT_ID") {
    docker compose run --rm --no-deps backend python -m app.channels.vapi.sync
    if ($LASTEXITCODE -ne 0) { Fail "Updating assistant A failed (message above)." }
    $updated++
}
if ($updated -eq 0) { Fail "Neither VAPI_ASSISTANT_ID nor VAPI_ASSISTANT_ID_B is set in .env." }

Step "6/6 Checking ESTARA answers through the tunnel..."
$ok = $false
for ($i = 0; $i -lt 30 -and -not $ok; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$url/api/v1/health/live" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $ok = $true }
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ok) { Fail "ESTARA is not reachable at $url yet. Wait a minute and run this again." }

Step "Checking every link (data, tunnel, Vapi assistants, last call)..."
docker compose run --rm --no-deps backend python -m app.channels.vapi.doctor
if ($LASTEXITCODE -ne 0) { Fail "The check above found a problem; its Fix line says what to do." }

Write-Host ""
Write-Host "READY. Open $site and click Talk to Priya." -ForegroundColor Green
Write-Host "Dashboard: http://localhost:3000   Stop: .\stop-demo.ps1"
Write-Host "Keep this PC awake and online during the demo."
