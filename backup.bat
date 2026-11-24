@echo off
rem Takes an encrypted, verified backup now (into backend\data\backups).
cd /d "%~dp0backend"
call .venv\Scripts\activate.bat
python -m app.cli backup
pause
