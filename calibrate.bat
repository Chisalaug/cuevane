@echo off
setlocal enabledelayedexpansion
%SystemRoot%\System32\chcp.com 65001 >nul 2>nul
title stim watcher  ^|  calibrate
cd /d "%~dp0"

echo  calibrate: sample 8s of the screen, print glyph metrics, no decision.
echo.
set "PY="
call :try "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try "python"
if not defined PY goto NOPY

"!PY!" watcher.py --calibrate
echo.
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
pause
exit /b 1
