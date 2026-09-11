@echo off
setlocal EnableExtensions
title Silicate Desktop
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  where py >nul 2>nul
  if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
)
if not exist "%PY%" goto :fail
"%PY%" -c "import tkinter" >nul 2>nul
if errorlevel 1 goto :fail
"%PY%" -m glass_membrane.desktop %*
if errorlevel 1 goto :fail
exit /b 0
:fail
echo Silicate could not start. Install Python 3.11 or newer with Tcl/Tk support.
echo Any additional error details appear above.
pause
exit /b 1
