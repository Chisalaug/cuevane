@echo off
setlocal enabledelayedexpansion
%SystemRoot%\System32\chcp.com 65001 >nul 2>nul
title stim watcher  ^|  pixel reader
cd /d "%~dp0"

echo.
echo  ================================================================
echo    stim watcher - reads arrows from the screen (canvas tasks)
echo    posts stim to  http://127.0.0.1:8765/evt
echo    keep this window on the 2nd screen; never cover the test page
echo  ================================================================
echo.

set "PY="
call :try "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try "python"
if not defined PY goto NOPY

echo  [i] python: !PY!
echo.
"!PY!" watcher.py
echo.
echo  [i] watcher exited
pause
exit /b 0

:try
if defined PY exit /b 0
if not exist "%~1" if not "%~1"=="python" exit /b 0
"%~1" -c "import mss, numpy" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b 0

:NOPY
echo  [x] no python with mss + numpy found
echo      fix: python -m pip install mss numpy
pause
exit /b 1
