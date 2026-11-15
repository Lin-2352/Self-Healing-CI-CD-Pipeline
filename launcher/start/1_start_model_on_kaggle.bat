@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Start the Qwen model on Kaggle (one click)
echo === Start the Qwen model on Kaggle (one click) ===
echo.
echo Project kernels on Kaggle (nothing should be RUNNING or QUEUED):
python kaggle\tools\status.py
echo.
echo [1/4] Building the endpoint notebook...
python kaggle\tools\build_endpoint.py || goto fail
echo [2/4] Pushing it to Kaggle (push.py refuses if another session is running)...
python kaggle\tools\push.py endpoint || goto fail
echo [3/4] Watching the boot. It takes about 4 minutes; leave this window open.
python kaggle\tools\watch_deploy.py || goto fail
echo [4/4] Updating QWEN_BASE_URL in the cloud n8n...
python tools\golive\set_cloud_qwen_url.py
if errorlevel 2 echo       (skipped: see the note above; the model itself is running)
if errorlevel 1 if not errorlevel 2 echo       FAILED to update n8n: set QWEN_BASE_URL by hand under Variables.
echo.
echo Model is up. Its URL is saved in %LOCALAPPDATA%\selfheal-n8n\qwen_url.txt
echo When you are done, run end\stop_model_on_kaggle.bat so it stops using GPU hours.
goto end
:fail
echo.
echo FAILED - read the messages above.
:end
echo.
pause
