@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\backup.py
) else (
  py -3 scripts\backup.py
)
set "backup_result=%errorlevel%"
if not "%backup_result%"=="0" echo Backup failed. Please ask the agent to inspect the output above.
pause
exit /b %backup_result%
