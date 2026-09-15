@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title KP NEXORA - Local Server
set "KP_NEXORA_LOCAL_HTTP=true"
set "OAUTHLIB_INSECURE_TRANSPORT=1"
set "OAUTHLIB_RELAX_TOKEN_SCOPE=1"
color 0B

echo.
echo  ==================================================
echo            KP NEXORA  -  LOCAL STARTUP
echo  ==================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python was not found in PATH.
  echo Install Python 3.11 or newer, then run this file again.
  pause
  exit /b 1
)

python -c "import sys; print('[OK] Python', sys.version.split()[0])"
if errorlevel 1 goto :fail

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo [OK] Created .env from .env.example
) else (
  echo [OK] Using existing .env
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 goto :fail
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :fail

 echo [2/3] Updating pip and installing dependencies...
python -m pip install --upgrade pip
if errorlevel 1 goto :fail
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

 echo [3/3] Checking KP NEXORA imports...
python -c "import flask,pandas,numpy; print('[OK] Core Python packages loaded')"
if errorlevel 1 goto :fail
python -m py_compile app.py
if errorlevel 1 goto :fail

 echo.
echo  ==================================================
echo   KP NEXORA is ready
echo   Local URL: http://127.0.0.1:5000
echo   Setup check: http://127.0.0.1:5000/setup-check
echo  ==================================================
echo.
start "" "http://127.0.0.1:5000"
python app.py
set ERR=%ERRORLEVEL%
echo.
echo Server stopped with exit code %ERR%.
pause
exit /b %ERR%

:fail
echo.
echo [ERROR] Startup failed. The exact error is shown above.
echo Do not close this window; take a screenshot of the error.
pause
exit /b 1
