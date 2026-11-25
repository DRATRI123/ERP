@echo off
rem Get the newest code and start the ERP. Only what changed is reinstalled or rebuilt.
setlocal EnableExtensions
title Private ERP - update
cd /d "%~dp0"
where git >nul 2>nul || (echo Git is not installed - install it from https://git-scm.com/ & pause & exit /b 1)
if not exist ".git" (echo This folder is not a git copy - download the new code by hand, then run start.bat. & pause & exit /b 1)

echo Stopping the ERP...
taskkill /FI "WINDOWTITLE eq ERP - *" /T /F >nul 2>nul

echo Getting the newest code...
git pull --ff-only
if errorlevel 1 (
  echo.
  echo  Could not update automatically. Usually a file was changed on this computer.
  echo  Run "git status" here to see which; "git stash" puts your changes aside, then run update.bat again.
  pause
  exit /b 1
)
call "%~dp0start.bat"
