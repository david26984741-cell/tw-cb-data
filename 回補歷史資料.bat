@echo off
cd /d "%~dp0"
title TPEx CB - full backfill

echo ============================================================
echo  FULL BACKFILL: 2007-01-02 to today
echo  About 4,900 files. Takes several hours.
echo.
echo  You can close this window at any time.
echo  Running it again skips files already downloaded.
echo ============================================================
echo.

python scripts\tpex_cb.py backfill %*

echo.
echo ============================================================
echo  Finished.
echo ============================================================
echo.
pause
