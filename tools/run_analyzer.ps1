# ============================================================================
# Delta-Neutral Bot — One-Click Analyzer Launcher
# ============================================================================
# Pulls fresh bot.db + bot.log from the VPS, optionally also from a local
# parallel paper bot, then launches the Streamlit analyzer.
#
# Usage:
#     Right-click → Run with PowerShell
#     OR from a PowerShell window:
#     .\run_analyzer.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

# ── Config ──────────────────────────────────────────────────────────────────
$VpsUser     = "root"
$VpsHost     = "139.59.231.196"
$VpsBotPath  = "/root/delta_neutral_bot"
$LocalBotDb  = "C:\Users\VISAL\Desktop\TO be VPS\delta_neutral_bot_v2\delta_neutral_bot\data\bot.db"
$LocalBotLog = "C:\Users\VISAL\Desktop\TO be VPS\delta_neutral_bot_v2\delta_neutral_bot\logs\bot.log"
$WorkDir     = $PSScriptRoot
$DataDir     = Join-Path $WorkDir "analyzer_data"

# ── Setup ───────────────────────────────────────────────────────────────────
Set-Location $WorkDir

if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir | Out-Null
}

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Delta-Neutral Bot Analyzer Launcher" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: dependency check ────────────────────────────────────────────────
Write-Host "[1/4] Checking Python dependencies..." -ForegroundColor Yellow
$pkgs = @("streamlit", "pandas", "plotly")
$missing = @()
foreach ($p in $pkgs) {
    $check = python -c "import $p" 2>&1
    if ($LASTEXITCODE -ne 0) { $missing += $p }
}
if ($missing.Count -gt 0) {
    Write-Host "  Installing missing: $($missing -join ', ')" -ForegroundColor Yellow
    pip install $missing
} else {
    Write-Host "  All dependencies present." -ForegroundColor Green
}

# ── Step 2: pull from VPS ───────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/4] Pulling bot.db + bot.log from VPS ($VpsHost)..." -ForegroundColor Yellow

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$vpsDb     = Join-Path $DataDir "vps_bot_$timestamp.db"
$vpsLog    = Join-Path $DataDir "vps_bot_$timestamp.log"

try {
    scp "${VpsUser}@${VpsHost}:$VpsBotPath/data/bot.db" "$vpsDb"
    if ($LASTEXITCODE -ne 0) { throw "scp DB failed" }
    Write-Host "  -> $vpsDb" -ForegroundColor Green

    scp "${VpsUser}@${VpsHost}:$VpsBotPath/logs/bot.log" "$vpsLog"
    if ($LASTEXITCODE -ne 0) { throw "scp log failed" }
    Write-Host "  -> $vpsLog" -ForegroundColor Green
} catch {
    Write-Host "  WARNING: VPS pull failed - $_" -ForegroundColor Red
    Write-Host "  Continuing anyway. You can still upload files manually." -ForegroundColor Yellow
}

# Make a stable "latest" copy so file pickers default to the right file
if (Test-Path $vpsDb) {
    Copy-Item $vpsDb (Join-Path $DataDir "vps_latest.db") -Force
}
if (Test-Path $vpsLog) {
    Copy-Item $vpsLog (Join-Path $DataDir "vps_latest.log") -Force
}

# ── Step 3: copy local paper bot DB (if it exists) ──────────────────────────
Write-Host ""
Write-Host "[3/4] Copying local paper bot DB..." -ForegroundColor Yellow
if (Test-Path $LocalBotDb) {
    $localDest = Join-Path $DataDir "local_latest.db"
    Copy-Item $LocalBotDb $localDest -Force
    Write-Host "  -> $localDest" -ForegroundColor Green
} else {
    Write-Host "  No local bot.db at $LocalBotDb (skipping)." -ForegroundColor DarkGray
}
if (Test-Path $LocalBotLog) {
    $localLogDest = Join-Path $DataDir "local_latest.log"
    Copy-Item $LocalBotLog $localLogDest -Force
}

# ── Step 4: launch Streamlit ────────────────────────────────────────────────
Write-Host ""
Write-Host "[4/4] Launching Streamlit analyzer..." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Files ready in: $DataDir" -ForegroundColor Cyan
Write-Host "    - vps_latest.db    (Bot A: VPS Live)" -ForegroundColor White
Write-Host "    - local_latest.db  (Bot B: Local Paper)" -ForegroundColor White
Write-Host ""
Write-Host "  Drag those into the Streamlit sidebar uploaders." -ForegroundColor Cyan
Write-Host "  Browser will open at http://localhost:8501" -ForegroundColor Cyan
Write-Host ""

# Open the data folder so user can drag files easily
Start-Process explorer.exe $DataDir

streamlit run (Join-Path $WorkDir "bot_analyzer.py")
