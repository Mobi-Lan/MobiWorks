@echo off
rem Dev run: serves ui/index.html directly and opens the app window. No build needed. (ASCII only)
rem Usage: run.cmd            -> real CLI (game must be on for sync)
rem        run.cmd --demo     -> demo data, no game/CLI needed
rem        run.cmd --nocli    -> real data folder, CLI calls blocked (safe UI check while game is on)
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set MOBIW_DEV=1
if /i "%~1"=="--demo" set MOBIW_DEMO=1
if /i "%~1"=="--nocli" set MOBIW_NO_CLI=1
echo MobiWorks dev server: http://127.0.0.1:19995  (Ctrl+C to stop; edit ui\index.html then refresh the window)
python server.py
echo.
echo Server stopped. The app window (if still open) is now stale - close it too.
