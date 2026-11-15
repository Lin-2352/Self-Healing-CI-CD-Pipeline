@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Start n8n on this PC only
echo === Start n8n on this PC only ===
echo.
echo Starts n8n without the tunnel. Nothing outside this PC can reach it and GitHub is not changed.
echo.
python tools\golive\up.py --local || (echo FAILED - read the messages above. & goto end)
start "" http://localhost:5678
:end

echo.
pause
