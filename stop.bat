@echo off
rem Stops every "ERP - ..." window started by start.bat.
taskkill /FI "WINDOWTITLE eq ERP - *" /T /F >nul 2>nul
echo Private ERP stopped.
timeout /t 2 >nul
