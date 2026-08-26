@echo off
cd /d "%~dp0"
title Push to GitHub

echo ============================================================
echo  Push code to GitHub
echo  (code only - this does NOT download any data)
echo ============================================================
echo.

git add -A
git commit -m "Add GitHub Actions workflows and fix TPEx SSL handling"
if errorlevel 1 echo [info] Nothing new to commit.

echo.
echo --- pulling ---
git pull --rebase --autostash

echo.
echo --- pushing ---
git push

echo.
echo ============================================================
echo  Done.
echo.
echo  Next step - open this page in your browser:
echo    https://github.com/david26984741-cell/tw-cb-data/actions
echo.
echo  Pick the backfill workflow on the left,
echo  then click "Run workflow" on the right.
echo ============================================================
echo.
pause
