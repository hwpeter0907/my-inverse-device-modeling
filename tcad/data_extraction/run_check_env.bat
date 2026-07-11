@echo off
setlocal
cd /d "%~dp0"

echo [1/1] Checking devsim_env for data_extraction...
conda run -n devsim_env python scripts\check_environment.py
if errorlevel 1 (
  echo.
  echo Environment check failed. Fix the message above, then run this file again.
  pause
  exit /b 1
)

echo.
echo Environment check passed.
pause
