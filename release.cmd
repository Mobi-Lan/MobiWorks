@echo off
rem Release: build the embedded-Python zip, then stage+verify release\ via release.py. ASCII only.
rem Usage: release.cmd <base-url> [--force]      e.g. release.cmd https://wk.example.com
rem The release is MobiWorks_Beta-<ver>.zip (tools\build_embed.py).
rem latest.json "url" is the download PAGE (<base-url>/#download), never the zip - see release.py.
rem build_embed.py must run on the official Python 3.12.10 (tkinter pieces are copied from it).
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if "%~1"=="" ( echo USAGE: release.cmd ^<base-url^> [--force] & exit /b 1 )
python tools\build_embed.py --out-dir build\embed-out
if errorlevel 1 ( echo BUILD FAILED & exit /b 1 )
python release.py %*
if errorlevel 1 ( exit /b 1 )
exit /b 0
