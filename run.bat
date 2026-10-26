@echo off
cd /d "%~dp0"

set "LOG_DIR=C:\Users\slong\Desktop\Dev\logs terminal"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "LOG_FILE=%LOG_DIR%\run_latest.log"

echo ========================================================
echo  Nexus Panel - Logs rediriges vers :
echo  %LOG_FILE%
echo ========================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "& { try { & '.venv\Scripts\python.exe' -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload } catch { $_ } } 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"

pause