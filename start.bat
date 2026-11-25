@echo off
setlocal enabledelayedexpansion
%SystemRoot%\System32\chcp.com 65001 >nul 2>nul
title test helper  ^|  guided launcher
cd /d "%~dp0"

echo.
echo  ================================================================
echo    test helper - guided launcher
echo    1) check python deps
echo    2) walk you through config (signs / keys / mode)
echo    3) start server + watcher
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

"!PY!" setup.py --ensure
if errorlevel 1 goto NOPY

"!PY!" -c "import mss, numpy" >nul 2>nul
if errorlevel 1 (
  echo  [!] mss / numpy missing, watcher skipped.
  echo      fix: "!PY!" -m pip install mss numpy
) else (
  start "stim watcher" /D "%~dp0" cmd /c start_watcher.bat
)
echo.

"!PY!" server.py
set "RC=%errorlevel%"
echo.
echo  [i] server exited, code=%RC%
pause
exit /b %RC%

:try
if defined PY exit /b 0
if not exist "%~1" if not "%~1"=="python" exit /b 0
"%~1" -c "import flask, pynput" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b 0

:NOPY
echo  [x] no python with flask + pynput found.
echo      fix: python -m pip install flask pynput
pause
exit /b 1
