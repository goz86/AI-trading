@echo off
setlocal EnableDelayedExpansion
title AI Trading Studio
cd /d "%~dp0"

call "%~dp0START_TRADING.bat"
exit /b %errorlevel%
