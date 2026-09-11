@echo off
rem Glass Membrane launcher: sets up on first run, checks everything, then opens Silicate.
rem   Double-click to start in LIVE mode (requests use your Claude / Codex subscriptions).
rem   Options:  --demo        simulated models, no provider usage
rem             --check-only  run the checks and exit
setlocal EnableExtensions
title Glass Membrane
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "MODE="
set "CHECKONLY="
for %%A in (%*) do (
  if /i "%%~A"=="--demo" set "MODE=--demo"
  if /i "%%~A"=="--check-only" set "CHECKONLY=1"
)

if not exist "%PY%" (
  echo First run: creating the Python environment...
  "%SystemRoot%\System32\where.exe" py >nul 2>nul
  if not errorlevel 1 (py -3 -m venv .venv) else (python -m venv .venv)
  if not exist "%PY%" (
    echo Could not create the Python environment. Install Python 3.11 or newer from https://www.python.org and try again.
    goto :fail
  )
  echo Installing Glass Membrane...
  "%PY%" -m pip install --quiet --upgrade pip
  "%PY%" -m pip install --quiet -e .
  if errorlevel 1 (
    echo Installing Glass Membrane failed.
    goto :fail
  )
)

echo.
echo Checking that everything is ready...
"%PY%" -m glass_membrane %MODE% doctor
if errorlevel 1 goto :fail
if defined CHECKONLY goto :end

echo.
echo Starting Silicate. Keep this window open while you use it; press Ctrl+C here to stop.
"%PY%" -m glass_membrane %MODE% ui
goto :end

:fail
echo.
echo Glass Membrane did not start. See the messages above.
if not defined CHECKONLY pause
endlocal & exit /b 1

:end
endlocal & exit /b 0
