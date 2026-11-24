@echo off
setlocal EnableExtensions
title Private ERP
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" goto :needsetup
if not exist "frontend\.next\BUILD_ID" goto :needsetup

echo Starting Private ERP...
call "backend\.venv\Scripts\activate.bat"
pushd backend
python -m app.cli migrate >nul || (popd & echo Database update failed - run setup.bat & pause & exit /b 1)
popd

rem Each part runs in its own minimised window; close them (or run stop.bat) to stop the ERP.
start "ERP - API" /min cmd /k "cd /d "%~dp0backend" && call .venv\Scripts\activate.bat && python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000"
start "ERP - worker" /min cmd /k "cd /d "%~dp0backend" && call .venv\Scripts\activate.bat && python -m app.cli worker"
start "ERP - web" /min cmd /k "cd /d "%~dp0frontend" && npm run start"

rem Live updates need Redis; started only when backend\.env has ERP_REDIS_URL=redis://...
set "REDIS="
for /f "tokens=1,* delims==" %%a in ('findstr /B /C:"ERP_REDIS_URL=redis" "backend\.env" 2^>nul') do set "REDIS=%%b"
if defined REDIS start "ERP - live updates" /min cmd /k "cd /d "%~dp0services\realtime" && set "ERP_REDIS_URL=%REDIS%" && set "ERP_API_URL=http://127.0.0.1:8000" && node dist\src\index.js"

echo Waiting for the web app...
for /l %%i in (1,1,60) do (
  powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://localhost:3000/login -TimeoutSec 2).StatusCode } catch { exit 1 }" >nul 2>nul && goto :open
  timeout /t 1 /nobreak >nul
)
:open
start "" http://localhost:3000
echo.
echo  Private ERP is running at http://localhost:3000
echo  To stop it, run stop.bat (or close the "ERP - ..." windows).
echo.
timeout /t 6 >nul
exit /b 0

:needsetup
echo Run setup.bat first.
pause
exit /b 1
