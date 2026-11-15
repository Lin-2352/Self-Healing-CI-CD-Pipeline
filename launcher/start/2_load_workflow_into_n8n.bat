@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Load the workflow and credentials into n8n
echo === Load the workflow and credentials into n8n ===
echo.
echo Stopping n8n first if it is running, so the import does not clash with it...
python tools\golive\down.py
echo.
echo Importing the workflow with the current model URL, and activating it...
python tools\golive\n8n_import.py || (echo FAILED - read the messages above. & goto end)
echo Done.
:end

echo.
pause
