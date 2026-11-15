@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Open the cloud n8n and connect it to the model
echo === Open the cloud n8n and connect it to the model ===
echo Use this when the local n8n (start_n8n_local_only.bat) does not work.
echo.
echo [1/3] Checking that the model answers...
python tools\golive\show_model_info.py
echo.
echo [2/3] Telling the cloud n8n workflow where the model is...
python tools\golive\set_cloud_qwen_url.py
if errorlevel 1 echo       Could not update the cloud n8n. Is the Kaggle model running? Run 1_start_model_on_kaggle.bat first.
echo.
echo [3/3] Opening the cloud workflow in your browser...
start "" https://self-heal.app.n8n.cloud/workflow/RqeD38ZnSeV64SHd
echo.
echo The cloud workflow "Self-Healing CI/CD Pipeline (Qwen3.8)" is already loaded and published.
echo To load it again in a fresh n8n (workflow only), run this and paste with Ctrl+V on the canvas:
echo   clip ^< n8n\self_healing_pipeline.json
echo Credentials cannot be loaded by a script on the cloud trial (its API is off): create them in the page
echo (Credentials, Create): "N8N Webhook Secret" (Header Auth, name X-Webhook-Secret),
echo "GitHub PAT" (Bearer) and "Qwen3.8 Endpoint Key" (Bearer, selfheal-e2e-key).
echo.
pause
