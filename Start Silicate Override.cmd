@echo off
setlocal EnableExtensions
title Silicate Owner Override
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  where py >nul 2>nul
  if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
)
if not exist "%PY%" goto :fail
"%PY%" -m glass_membrane.override_desktop %*
if errorlevel 1 goto :fail
exit /b 0
:fail
echo Owner Override could not start. Check the error above and Python Tcl/Tk installation.
pause
exit /b 1
