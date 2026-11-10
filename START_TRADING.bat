@echo off
setlocal EnableDelayedExpansion
title AI Trading Studio
cd /d "%~dp0"

echo ====================================================================
echo       AI TRADING STUDIO - HE THONG GIAO DICH DA TAC TU
echo ====================================================================
echo.

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else if exist "C:\Users\junwi\Downloads\Telegram Desktop\TradingAgents-5PhutTrading\.venv\Scripts\python.exe" (
    set "PY=C:\Users\junwi\Downloads\Telegram Desktop\TradingAgents-5PhutTrading\.venv\Scripts\python.exe"
) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
) else if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    for %%i in (python.exe) do set "PY=%%~$PATH:i"
)

if not defined PY (
    echo [LOI] Khong tim thay Python tren he thong.
    echo Vui long cai dat Python 3.11 hoac 3.12.
    pause
    exit /b 1
)

echo [Python] Su dung: !PY!
echo.

echo [1/2] Dang kiem tra may chu Localhost (Cong 8888)...
set "ALIVE=0"
powershell -NoProfile -NonInteractive -Command "try { $r = (Invoke-WebRequest -Uri 'http://127.0.0.1:8888/api/status' -TimeoutSec 1 -UseBasicParsing).StatusCode; if ($r -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 set "ALIVE=1"

if "!ALIVE!"=="1" (
    echo       [OK] May chu Localhost da san sang!
) else (
    echo       Dang bat may chu app_server.py...
    start "AI Trading Local Server" /min "!PY!" "%~dp0app_server.py" --port 8888
    
    for /L %%A in (1,1,10) do (
        if "!ALIVE!"=="0" (
            powershell -NoProfile -NonInteractive -Command "Start-Sleep -Milliseconds 600" >nul 2>&1
            powershell -NoProfile -NonInteractive -Command "try { $r = (Invoke-WebRequest -Uri 'http://127.0.0.1:8888/api/status' -TimeoutSec 1 -UseBasicParsing).StatusCode; if ($r -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
            if !errorlevel! equ 0 set "ALIVE=1"
        )
    )
    if "!ALIVE!"=="1" (
        echo       [OK] May chu da khoi dong thanh cong!
    ) else (
        echo       [Luu y] May chu dang hoan tat khoi dong ngam...
    )
)

echo.
echo [2/2] Dang mo giao dien AI Trading Studio tren trinh duyet...
start http://127.0.0.1:8888

echo.
echo ====================================================================
echo   KHOI DONG HOAN TAT!
echo   Dia chi web: http://127.0.0.1:8888
echo   Cua so nay se tu dong dong sau 3 giay.
echo ====================================================================
echo.
powershell -NoProfile -NonInteractive -Command "Start-Sleep -Seconds 3" >nul 2>&1
exit /b 0
