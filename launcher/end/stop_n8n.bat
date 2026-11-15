@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Stop n8n and the tunnel
echo === Stop n8n and the tunnel ===
echo.
python tools\golive\down.py

echo.
pause
