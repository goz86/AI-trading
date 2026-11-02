@echo off
chcp 65001 >nul
cd /d "%~dp0"
for %%f in (*3D*.bat) do call "%~dp0%%f"
