# ====================================================================
#   AI TRADING STUDIO - STARTUP LAUNCHER
# ====================================================================
$ErrorActionPreference = "SilentlyContinue"

function Write-Safe([string]$msg, [string]$color = "") {
    try {
        if ($color -ne "") {
            Write-Host $msg -ForegroundColor $color
        } else {
            Write-Host $msg
        }
    } catch {
        [System.Console]::WriteLine($msg)
    }
}

Write-Safe "====================================================================" "Cyan"
Write-Safe "       AI TRADING STUDIO - HE THONG GIAO DICH DA TAC TU            " "Yellow"
Write-Safe "====================================================================" "Cyan"
Write-Safe ""

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) { $scriptDir = (Get-Location).Path }

$pythonCandidates = @(
    (Join-Path $scriptDir ".venv\Scripts\python.exe"),
    "C:\Users\junwi\Downloads\Telegram Desktop\TradingAgents-5PhutTrading\.venv\Scripts\python.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
)

$pyPath = $null
foreach ($cand in $pythonCandidates) {
    if (Test-Path $cand) {
        $pyPath = $cand
        break
    }
}

if (-not $pyPath) {
    $sysPy = (Get-Command python -ErrorAction SilentlyContinue)
    if ($sysPy) { $pyPath = $sysPy.Source }
}

if (-not $pyPath) {
    Write-Safe "[LOI] Khong tim thay Python tren he thong." "Red"
    Write-Safe "Vui long cai dat Python (3.11 - 3.12) de chay he thong." "Yellow"
    Read-Host "Nhan Enter de thoat..."
    exit 1
}

Write-Safe "[Python] Su dung: $pyPath" "Gray"
Write-Safe ""

Write-Safe "[1/2] Dang kiem tra may chu Localhost (Cong 8888)..." "White"
$isServerRunning = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8888/api/status" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -eq 200) { $isServerRunning = $true }
} catch {
    $isServerRunning = $false
}

if ($isServerRunning) {
    Write-Safe "      [OK] May chu Localhost da san sang hoat dong!" "Green"
} else {
    Write-Safe "      Dang bat may chu app_server.py..." "Yellow"
    
    $appServerPath = Join-Path $scriptDir "app_server.py"
    if (-not (Test-Path $appServerPath)) {
        Write-Safe "[LOI] Khong tim thay file app_server.py" "Red"
        Read-Host "Nhan Enter de thoat..."
        exit 1
    }

    Start-Process -FilePath "cmd.exe" -ArgumentList "/c title AI Trading Server && `"$pyPath`" `"$appServerPath`" --port 8888" -WorkingDirectory $scriptDir -WindowStyle Minimized

    $retries = 15
    while ($retries -gt 0 -and -not $isServerRunning) {
        Start-Sleep -Milliseconds 600
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8888/api/status" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { $isServerRunning = $true }
        } catch {
            $retries--
        }
    }

    if ($isServerRunning) {
        Write-Safe "      [OK] May chu da khoi dong thanh cong!" "Green"
    } else {
        Write-Safe "      [Luu y] May chu dang hoan tat khoi dong ngam..." "Yellow"
    }
}

Write-Safe ""
Write-Safe "[2/2] Dang mo giao dien AI Trading Studio tren trinh duyet..." "White"
Start-Process "http://127.0.0.1:8888"

Write-Safe ""
Write-Safe "====================================================================" "Cyan"
Write-Safe "  KHOI DONG HOAN TAT!" "Green"
Write-Safe "  Dia chi web: http://127.0.0.1:8888" "Yellow"
Write-Safe "====================================================================" "Cyan"
Write-Safe ""
Start-Sleep -Seconds 3
