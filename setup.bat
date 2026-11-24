@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Private ERP - setup
cd /d "%~dp0"
echo.
echo  ==============================================
echo   Private ERP - setup for this computer
echo  ==============================================
echo   Safe to run again: it also updates an existing install.
echo.

rem ---------------------------------------------------------------- prerequisites
where python >nul 2>nul
if errorlevel 1 (
  echo  [X] Python 3.12 or newer is required.
  echo      Download it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  goto :fail
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
  echo  [X] Python 3.12 or newer is required ^(found an older version^).
  goto :fail
)
where node >nul 2>nul
if errorlevel 1 (
  echo  [X] Node.js 22 LTS is required. Download it from https://nodejs.org/
  goto :fail
)

rem ---------------------------------------------------------------- backend
echo [1/6] Python packages (first run takes a few minutes)...
if not exist "backend\.venv\Scripts\python.exe" (
  python -m venv backend\.venv || goto :fail
)
call "backend\.venv\Scripts\activate.bat"
python -m pip install --quiet --upgrade pip || goto :fail
python -m pip install --quiet -e backend || goto :fail

echo [2/6] Settings and secret keys...
python backend\scripts\init_env.py || goto :fail

echo [3/6] Database...
pushd backend
python -m app.cli migrate || (popd & goto :fail)

echo [4/6] Owner account...
python -m app.cli owner-exists >nul 2>nul
if errorlevel 1 (
  echo   Create the owner account ^(you^). The password is typed hidden.
  set /p OWNER_EMAIL=  Your e-mail: 
  set /p OWNER_NAME=  Your full name: 
  python -m app.cli create-owner --email "!OWNER_EMAIL!" --name "!OWNER_NAME!"
  if errorlevel 1 (popd & goto :fail)
  echo.
  choice /C YN /N /M "  Add a sample 'Demo' business to explore? [Y/N] "
  if !errorlevel! EQU 1 (
    python scripts\demo_data.py --email "!OWNER_EMAIL!"
  )
) else (
  echo   Owner account already exists.
)
popd

rem ---------------------------------------------------------------- frontend
echo [5/6] Web app packages...
pushd frontend
call npm ci --no-audit --no-fund --loglevel=error || (popd & goto :fail)
echo [6/6] Building the web app...
call npm run build || (popd & goto :fail)
popd

rem Real-time service (only used when Redis is configured in backend\.env)
pushd services\realtime
call npm ci --no-audit --no-fund --loglevel=error >nul 2>nul && call npm run build >nul 2>nul
popd

echo.
echo  ==============================================
echo   Done. Double-click start.bat to open the ERP.
echo  ==============================================
echo   Your data and keys are in backend\data and backend\.env - back both up.
echo.
pause
exit /b 0

:fail
echo.
echo  Setup did not finish. Read the message above, fix it, and run setup.bat again.
pause
exit /b 1
