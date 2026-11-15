@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Stop ci-heal-backend
echo === Stop ci-heal-backend ===
echo.
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; 'backend stopped' } else { 'backend was not running' }"

echo.
pause
