# ====================================================================
#   AI TRADING STUDIO - STARTUP LAUNCHER
# ====================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$host.UI.RawUI.WindowTitle = "AI Trading Studio - Khởi Động Tự Động"

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "       AI TRADING STUDIO - VĂN PHÒNG 3D & HỆ THỐNG ĐA TÁC TỬ        " -ForegroundColor Yellow
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Tìm môi trường Python
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
    Write-Host "[LỖI] Không tìm thấy Python trên máy tính." -ForegroundColor Red
    Write-Host "Vui lòng cài đặt Python (3.11 - 3.12) để chạy hệ thống." -ForegroundColor Yellow
    Read-Host "Nhấn Enter để thoát..."
    exit 1
}

Write-Host "[Python] Sử dụng: $pyPath" -ForegroundColor Gray
Write-Host ""

# 2. Kiểm tra máy chủ Localhost cổng 8888
Write-Host "[1/2] Đang kiểm tra máy chủ Localhost (Cổng 8888)..." -ForegroundColor White
$isServerRunning = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8888/api/status" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        $isServerRunning = $true
    }
} catch {
    $isServerRunning = $false
}

if ($isServerRunning) {
    Write-Host "      ✓ Máy chủ Localhost (Cổng 8888) đã sẵn sàng hoạt động!" -ForegroundColor Green
} else {
    Write-Host "      Đang khởi động máy chủ app_server.py..." -ForegroundColor Yellow
    
    $appServerPath = Join-Path $scriptDir "app_server.py"
    if (-not (Test-Path $appServerPath)) {
        Write-Host "[LỖI] Không tìm thấy file app_server.py tại: $appServerPath" -ForegroundColor Red
        Read-Host "Nhấn Enter để thoát..."
        exit 1
    }

    # Chạy máy chủ ngầm trong cửa sổ riêng biệt để không bị ngắt khi đóng launcher
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c title AI Trading Server && `"$pyPath`" `"$appServerPath`" --port 8888" -WorkingDirectory $scriptDir -WindowStyle Minimized

    Write-Host "      Đang chờ máy chủ kết nối" -NoNewline -ForegroundColor Gray
    $retries = 15
    while ($retries -gt 0 -and -not $isServerRunning) {
        Start-Sleep -Milliseconds 600
        Write-Host "." -NoNewline -ForegroundColor Gray
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8888/api/status" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                $isServerRunning = $true
            }
        } catch {
            $retries--
        }
    }
    Write-Host ""

    if ($isServerRunning) {
        Write-Host "      ✓ Máy chủ đã khởi động thành công!" -ForegroundColor Green
    } else {
        Write-Host "      [Lưu ý] Máy chủ đang hoàn tất khởi động ngầm..." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[2/2] Đang mở giao diện AI Trading Studio trên trình duyệt..." -ForegroundColor White
Start-Process "http://127.0.0.1:8888"

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "  KHỞI ĐỘNG HOÀN TẤT!" -ForegroundColor Green
Write-Host "  Địa chỉ web: http://127.0.0.1:8888" -ForegroundColor Yellow
Write-Host "  Anh có thể thu nhỏ cửa sổ này và trải nghiệm trên trình duyệt." -ForegroundColor White
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""
Start-Sleep -Seconds 3
