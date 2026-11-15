@echo off
setlocal
rem project root is two folders up from this file
cd /d "%~dp0..\.."
title Break the demo app and push it
echo === Break the demo app on purpose and push it to the test repository ===
echo This changes "sum: a + b" to "sum: a - b" in demo-app\src\format.js, commits, and pushes to
echo Lin-2352/Self-Healing-CI-CD-Pipeline (the "lin" remote). GitHub Actions then fails and n8n heals it.
echo.
git switch main || goto fail
git pull lin main --ff-only || goto fail
python -c "p='demo-app/src/format.js'; s=open(p,encoding='utf-8').read(); assert 'sum: a + b' in s or 'sum: a - b' in s, 'sum line not found'; open(p,'w',encoding='utf-8',newline='').write(s.replace('sum: a + b','sum: a - b'))" || goto fail
git add demo-app/src/format.js || goto fail
git commit -m "Break the sum on purpose for the demo" || echo (nothing new to commit, pushing what is already committed)
git push lin main || goto fail
echo.
echo Pushed. Now watch: GitHub Actions (red), the n8n Executions tab, then Pull requests.
goto end
:fail
echo.
echo FAILED - read the messages above.
:end
echo.
pause
