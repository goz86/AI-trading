@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title AI Trading Studio - 3D Virtual Office

echo.
echo  ==================================================================
echo     AI TRADING STUDIO - THỊ TRẤN VĂN PHÒNG 3D & ĐA TÁC TỬ
echo  ==================================================================
echo   Chạy trực tiếp tại kho mã nguồn: %~dp0
echo.

set "GOCAI=%~dp0"
set "PYV=%GOCAI%\.venv\Scripts\python.exe"

REM --- 1. Kiểm tra môi trường ảo .venv đã có sẵn ---
if exist "%PYV%" goto START_SERVER

REM --- Kiểm tra nếu đã có .venv ở thư mục Telegram Desktop thì tận dụng ---
if exist "C:\Users\junwi\Downloads\Telegram Desktop\TradingAgents-5PhutTrading\.venv\Scripts\python.exe" (
    set "PYV=C:\Users\junwi\Downloads\Telegram Desktop\TradingAgents-5PhutTrading\.venv\Scripts\python.exe"
    goto START_SERVER
)

REM --- 2. Tìm kiếm Python trên hệ thống ---
echo  [1/3] Đang kiểm tra Python trên máy tính...
call :TIMPY
if defined PY goto CREATE_VENV

echo        Chưa có Python phù hợp (cần 3.11 - 3.14).
echo        Đang tự động cài đặt Python 3.12 qua winget...
where winget >nul 2>&1 && winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
call :TIMPY
if defined PY goto CREATE_VENV

echo        Đang tải bộ cài Python 3.12 trực tiếp từ python.org...
set "PYSETUP=%TEMP%\python-3.12.10-amd64.exe"
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $env:PYSETUP"
if exist "%PYSETUP%" (
    echo        Đang tiến hành cài đặt ngầm Python 3.12, vui lòng đợi 1 đến 2 phút...
    "%PYSETUP%" /quiet InstallAllUsers=0 InstallLauncherAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0 Shortcuts=0
    call :TIMPY
)

if not defined PY (
    echo.
    echo  [LỖI] Không tự cài được Python. Trang web tải Python sẽ mở ra.
    echo  Hãy tải bản 3.12, lúc cài NHỚ TÍCH VÀO Ô "Add python.exe to PATH".
    start "" https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

:CREATE_VENV
echo        Sử dụng Python: %PY%
echo.
echo  [2/3] Thiết lập môi trường ảo .venv cho dự án TradingAgents...
if not exist "%GOCAI%\.venv" %PY% -m venv "%GOCAI%\.venv"
set "PYV=%GOCAI%\.venv\Scripts\python.exe"
"%PYV%" -m pip install --upgrade pip -q
echo        Cài đặt gói thư viện TradingAgents...
"%PYV%" -m pip install -e "%GOCAI%"
if errorlevel 1 (
    echo  [Cảnh báo] Cài đặt gói gặp lỗi nhẹ, thử dùng gói dự phòng...
)

:START_SERVER
echo.
echo  [3/3] Đang khởi động máy chủ Web UI (Cổng 8888)...
start "" "%PYV%" "%GOCAI%\app_server.py" --port 8888

echo  Đang mở giao diện Văn phòng 3D trên trình duyệt...
powershell -NoProfile -Command "Start-Sleep -Milliseconds 1200; Start-Process 'http://127.0.0.1:8888'"

echo.
echo  ==================================================================
echo   GIAO DIỆN ĐÃ ĐƯỢC KHỞI CHẠY THÀNH CÔNG!
echo   Địa chỉ: http://127.0.0.1:8888
echo   Để tắt máy chủ, chỉ cần đóng cửa sổ này.
echo  ==================================================================
echo.
pause
exit /b 0

:TIMPY
set "PY="
for %%v in (3.13 3.12 3.14 3.11) do if not defined PY py -%%v -c "import sys" >nul 2>&1 && set "PY=py -%%v"
if not defined PY python -c "import sys;sys.exit(0 if (3,11)<=sys.version_info[:2]<=(3,14) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
exit /b 0

