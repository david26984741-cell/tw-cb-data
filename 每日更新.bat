@echo off
cd /d "%~dp0"
title TPEx CB - daily update

REM Fetches only the missing files for the current month.
REM Safe to run repeatedly - already-downloaded files are skipped.
REM This is the file to point Windows Task Scheduler at,
REM but only if you decide NOT to use GitHub Actions.

python scripts\tpex_cb.py daily >> data\daily_log.txt 2>&1
