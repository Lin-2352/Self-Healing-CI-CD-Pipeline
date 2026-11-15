@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Stop the Qwen model on Kaggle
echo === Stop the Qwen model on Kaggle ===
echo.
echo The Kaggle command line cannot stop a running session, so this opens kaggle.com.
echo There: View Active Events, then the ... menu on the session, then Stop Session.
start "" https://www.kaggle.com/code/lacrimatct/qwen3-8-27b-live-endpoint
echo.
echo Press a key once you have stopped it, to check the status and save the logs.
pause >nul
python kaggle\tools\status.py
python kaggle\tools\fetch_logs.py endpoint

echo.
pause
