@echo off
rem Put the ERP on the internet while this window is open (free Cloudflare tunnel).
rem Needed for the WhatsApp catalogue and the salesperson portal on phones outside this computer.
title Private ERP - online
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is required. & pause & exit /b 1)
python tools\online.py
pause
