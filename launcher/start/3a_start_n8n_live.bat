@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Start n8n with the public webhook
echo === Start n8n with the public webhook ===
echo.
echo This starts n8n, opens a public Cloudflare tunnel to it, and points this repo's
echo N8N_WEBHOOK_URL secret at that tunnel. It needs "gh auth login" done once.
echo.
python tools\golive\up.py || (echo FAILED - read the messages above. & goto end)
start "" http://localhost:5678
:end

echo.
pause
