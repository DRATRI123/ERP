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

rem ---------------------------------------------------------------- packages and web app
echo [1/5] Packages and web app (first run takes several minutes; later runs only redo what changed)...
if not exist "backend\.venv\Scripts\python.exe" (
  python -m venv backend\.venv || goto :fail
  call "backend\.venv\Scripts\activate.bat"
  python -m pip install --quiet --upgrade pip || goto :fail
) else (
  call "backend\.venv\Scripts\activate.bat"
)
call tools\refresh.bat || goto :fail

echo [2/5] Settings and secret keys...
python backend\scripts\init_env.py || goto :fail

echo [3/5] Database...
pushd backend
python -m app.cli migrate || (popd & goto :fail)

echo [4/5] Owner account...
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

echo.
echo  ==============================================
echo   Done. Double-click start.bat to open the ERP.
echo   To get new code later: double-click update.bat ^(git pull + start^).
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
