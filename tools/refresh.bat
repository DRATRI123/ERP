@echo off
rem Bring packages and the web build up to date, redoing only what changed since last time.
rem Called by setup.bat, start.bat and update.bat with the Python environment already active.
setlocal EnableExtensions
cd /d "%~dp0.."

python tools\stamp.py changed python --requires backend\.venv\Scripts\python.exe -- backend\pyproject.toml
if errorlevel 10 (
  echo   Python packages changed - installing...
  python -m pip install --quiet -e backend || exit /b 1
  python tools\stamp.py save python -- backend\pyproject.toml
) else (echo   Python packages: up to date)

python tools\stamp.py changed npm --requires frontend\node_modules -- frontend\package-lock.json
if errorlevel 10 (
  echo   Web app packages changed - installing...
  pushd frontend
  call npm ci --prefer-offline --no-audit --no-fund --loglevel=error || (popd & exit /b 1)
  popd
  python tools\stamp.py save npm -- frontend\package-lock.json
) else (echo   Web app packages: up to date)

python tools\stamp.py changed web --requires frontend\.next\BUILD_ID -- frontend
if errorlevel 10 (
  echo   Web app code changed - building ^(1-3 minutes, only after an update^)...
  pushd frontend
  call npm run build || (popd & exit /b 1)
  popd
  python tools\stamp.py save web -- frontend
) else (echo   Web app build: up to date)

rem Real-time service: optional (only used with Redis), so a failure here never stops the ERP.
python tools\stamp.py changed realtime --requires services\realtime\dist -- services\realtime
if errorlevel 10 (
  pushd services\realtime
  call npm ci --prefer-offline --no-audit --no-fund --loglevel=error >nul 2>nul && call npm run build >nul 2>nul && (popd & python tools\stamp.py save realtime -- services\realtime) || popd
)
exit /b 0
