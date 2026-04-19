# IBKR Portfolio Dashboard — Windows Installer
# ─────────────────────────────────────────────
# Run this once in PowerShell (no admin required):
#   irm https://raw.githubusercontent.com/GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard/main/install.ps1 | iex

$ErrorActionPreference = "Stop"
$REPO = "GameMaster301/IBKR-TWS-Web-Portfolio-Dashboard"
$RAW  = "https://raw.githubusercontent.com/$REPO/main"
$DIR  = "$HOME\ibkrdash"

Clear-Host
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║     IBKR Portfolio Dashboard             ║" -ForegroundColor Cyan
Write-Host "  ║     Installer                            ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Check Docker ───────────────────────────────────────────────────
Write-Host "  [1/4] Checking Docker Desktop..." -NoNewline
$dockerOk = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch {}

if (-not $dockerOk) {
    Write-Host " NOT FOUND" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Docker Desktop is required and takes ~2 minutes to install." -ForegroundColor Yellow
    Write-Host "  Download it from: https://www.docker.com/products/docker-desktop/" -ForegroundColor Yellow
    Write-Host ""
    $open = Read-Host "  Open the Docker Desktop download page now? [Y/n]"
    if ($open -ne 'n' -and $open -ne 'N') {
        Start-Process "https://www.docker.com/products/docker-desktop/"
    }
    Write-Host ""
    Write-Host "  After installing Docker Desktop, run this installer again." -ForegroundColor Cyan
    Write-Host ""
    exit 1
}
Write-Host " OK" -ForegroundColor Green

# ── Step 2: Create install folder ─────────────────────────────────────────
Write-Host "  [2/4] Setting up $DIR..." -NoNewline
New-Item -ItemType Directory -Force -Path $DIR | Out-Null
Write-Host " OK" -ForegroundColor Green

# ── Step 3: Download config files ─────────────────────────────────────────
Write-Host "  [3/4] Downloading files..." -NoNewline
Invoke-WebRequest "$RAW/docker-compose.yml" -OutFile "$DIR\docker-compose.yml" -UseBasicParsing

if (-not (Test-Path "$DIR\.env")) {
    @"
# ── IBKR connection ────────────────────────────────────────────────────
# Set IBKR_PORT to match your IB Gateway / TWS port:
#   IB Gateway paper:  4002  (default)
#   IB Gateway live:   4001
#   TWS paper:         7497
#   TWS live:          7496
IBKR_HOST=host.docker.internal
IBKR_PORT=4002
IBKR_CLIENT_ID=10
IBKR_READONLY=true

# ── Dashboard ──────────────────────────────────────────────────────────
DASH_PORT=8050
OPEN_BROWSER=0
"@ | Out-File -Encoding utf8 "$DIR\.env"
}
Write-Host " OK" -ForegroundColor Green

# ── Step 4: Create helper scripts + desktop shortcut ──────────────────────
Write-Host "  [4/4] Creating shortcuts..." -NoNewline

@"
@echo off
title IBKR Dashboard
cd /d "%~dp0"
echo Starting IBKR Portfolio Dashboard...
docker compose up -d
if errorlevel 1 (
    echo.
    echo  ERROR: Could not start the dashboard.
    echo  Make sure Docker Desktop is running (whale icon in taskbar).
    pause
    exit /b 1
)
timeout /t 4 /nobreak >nul
start http://localhost:8050
echo.
echo  Dashboard is running at http://localhost:8050
echo  Close this window at any time — the dashboard keeps running.
echo.
pause
"@ | Out-File -Encoding ascii "$DIR\start.bat"

@"
@echo off
title IBKR Dashboard — Stop
cd /d "%~dp0"
echo Stopping IBKR Portfolio Dashboard...
docker compose down
echo Done.
pause
"@ | Out-File -Encoding ascii "$DIR\stop.bat"

@"
@echo off
title IBKR Dashboard — Update
cd /d "%~dp0"
echo Updating IBKR Portfolio Dashboard to the latest version...
docker compose pull
docker compose up -d
echo.
echo  Update complete. Dashboard restarted at http://localhost:8050
pause
"@ | Out-File -Encoding ascii "$DIR\update.bat"

# Desktop shortcut
try {
    $sh = New-Object -ComObject WScript.Shell
    $sc = $sh.CreateShortcut("$HOME\Desktop\IBKR Dashboard.lnk")
    $sc.TargetPath       = "$DIR\start.bat"
    $sc.WorkingDirectory = $DIR
    $sc.IconLocation     = "shell32.dll,14"
    $sc.Description      = "Start IBKR Portfolio Dashboard"
    $sc.Save()
    Write-Host " OK" -ForegroundColor Green
} catch {
    Write-Host " (shortcut skipped)" -ForegroundColor Yellow
}

# ── Done ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║   Installation complete!                 ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  HOW TO USE" -ForegroundColor Cyan
Write-Host "  ──────────"
Write-Host "  1. Open IB Gateway (or TWS) and log in"
Write-Host "  2. Double-click  'IBKR Dashboard'  on your Desktop"
Write-Host "  3. Dashboard opens at  http://localhost:8050"
Write-Host ""
Write-Host "  To change the port:  edit $DIR\.env" -ForegroundColor DarkGray
Write-Host "  To update later:     double-click $DIR\update.bat" -ForegroundColor DarkGray
Write-Host ""

$launch = Read-Host "  Launch the dashboard now? [Y/n]"
if ($launch -ne 'n' -and $launch -ne 'N') {
    Write-Host ""
    Write-Host "  Pulling image (first run may take a minute)..." -ForegroundColor Cyan
    Set-Location $DIR
    docker compose pull
    docker compose up -d
    Start-Sleep 5
    Start-Process "http://localhost:8050"
    Write-Host ""
    Write-Host "  Dashboard is live at http://localhost:8050" -ForegroundColor Green
    Write-Host ""
}
