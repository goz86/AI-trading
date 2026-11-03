@echo off
setlocal
title AI Trading Studio
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_trading.ps1"
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Launch failed. Please check your Python installation.
    pause
)
