@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Start ci-heal-backend
echo === Start ci-heal-backend ===
echo.
echo Writing the model URL and key into ci-heal-backend\.env and checking the model answers...
python kaggle\tools\backend_env.py --check || echo WARNING: model check failed; the backend will start, but model calls will fail.
echo.
cd ci-heal-backend
if not exist .venv\Scripts\python.exe (echo ci-heal-backend\.venv is missing - follow the Quick start in ci-heal-backend\README.md & goto end)
start "" http://127.0.0.1:8000/docs
echo Backend runs in this window on http://127.0.0.1:8000 - close the window or run end\stop_backend.bat to stop it.
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
:end

echo.
pause
