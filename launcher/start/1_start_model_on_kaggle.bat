@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Start the Qwen model on Kaggle
echo === Start the Qwen model on Kaggle ===
echo.
echo Project kernels on Kaggle (nothing should be RUNNING or QUEUED):
python kaggle\tools\status.py
echo.
echo Building the endpoint notebook...
python kaggle\tools\build_endpoint.py || goto fail
echo Pushing it to Kaggle (push.py refuses if another session is running)...
python kaggle\tools\push.py endpoint || goto fail
echo Watching the boot. It takes about 4 minutes; leave this window open until it prints the URL.
python kaggle\tools\watch_deploy.py || goto fail
echo.
echo Model is up. Its URL is saved in %LOCALAPPDATA%\selfheal-n8n\qwen_url.txt
goto end
:fail
echo.
echo FAILED - read the messages above.
:end

echo.
pause
