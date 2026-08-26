@echo off
cd /d "%~dp0"
title Test run - 2007 Jan to Mar

echo ============================================================
echo  TEST RUN: fetch 2007-01 to 2007-03 only
echo  Takes a few minutes. Files land in data\quotes\2007\
echo ============================================================
echo.

python scripts\tpex_cb.py backfill --start 2007-01 --end 2007-03

echo.
echo ============================================================
echo  Finished.
echo ============================================================
echo.
pause
