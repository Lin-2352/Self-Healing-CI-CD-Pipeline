@echo off
setlocal
cd /d "%~dp0..\.."
title One-time setup: cloud n8n API key
echo === One-time setup: cloud n8n API key ===
echo.
echo 1. In your cloud n8n open Settings, then "n8n API", then "Create an API key".
echo 2. Copy the key and paste it at the hidden prompt below (nothing is shown as you type).
echo    It is stored only in %LOCALAPPDATA%\selfheal-n8n\cloud_api.json, never in the repository.
echo.
python tools\golive\set_cloud_qwen_url.py --save-key
echo.
echo Testing it against the current model URL (if a model has been started)...
python tools\golive\set_cloud_qwen_url.py
echo.
pause
