@echo off
REM Detiene el dashboard GeoPulse (puerto 8599).
setlocal
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8599" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
echo Dashboard detenido.
endlocal
