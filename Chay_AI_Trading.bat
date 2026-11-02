@echo off
chcp 65001 >nul
title AI Trading Studio - Khởi Động Tự Động
cd /d "%~dp0"

echo ====================================================================
echo       AI TRADING STUDIO - VĂN PHÒNG 3D & HỆ THỐNG ĐA TÁC TỬ
echo ====================================================================
echo.

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else if exist "C:\Users\junwi\Downloads\Telegram Desktop\TradingAgents-5PhutTrading\.venv\Scripts\python.exe" (
    set "PY=C:\Users\junwi\Downloads\Telegram Desktop\TradingAgents-5PhutTrading\.venv\Scripts\python.exe"
) else (
    where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo [LỖI] Không tìm thấy Python trên máy tính.
    echo Vui lòng cài đặt Python (3.11 - 3.12) để chạy hệ thống.
    pause
    exit /b 1
)

echo [1/2] Đang kiểm tra máy chủ Localhost (Cổng 8888)...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8888/api/status' -TimeoutSec 1; exit 0 } catch { exit 1 }"
if %errorlevel% equ 0 (
    echo       Máy chủ Localhost đã sẵn sàng hoạt động!
) else (
    echo       Đang bật máy chủ app_server.py...
    start "AI Trading Local Server" /min "%PY%" app_server.py --port 8888
    powershell -NoProfile -Command "Start-Sleep -Milliseconds 1500"
)

echo [2/2] Đang tự động mở giao diện AI Trading Studio trên trình duyệt...
start http://127.0.0.1:8888

echo.
echo ====================================================================
echo   KHỞI ĐỘNG HOÀN TẤT!
echo   Địa chỉ web: http://127.0.0.1:8888
echo   Anh có thể thu nhỏ cửa sổ này và trải nghiệm trên trình duyệt.
echo ====================================================================
echo.
pause
