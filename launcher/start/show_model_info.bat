@echo off
setlocal
cd /d "%~dp0..\.."
title Qwen model link and key
echo === Qwen model link and key ===
echo.
python tools\golive\show_model_info.py
echo.
pause
