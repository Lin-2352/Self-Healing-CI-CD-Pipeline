@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Stop everything
echo === Stop everything ===
echo.
call "%~dp0stop_backend.bat" <nul
call "%~dp0stop_n8n.bat" <nul
call "%~dp0stop_model_on_kaggle.bat"

echo.
pause
