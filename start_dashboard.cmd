@echo off
REM Lanza el dashboard GeoPulse en segundo plano (no bloquea la consola).
setlocal
set "PYTHONPATH=%~dp0src"
if not exist "%~dp0logs" mkdir "%~dp0logs"
start "GeoPulse Dashboard" /min cmd /c "python -m streamlit run src\geopulse\dashboard\app.py --server.headless=true --server.port=8599 --browser.gatherUsageStats=false > logs\streamlit.out 2>&1 < nul"
timeout /t 3 /nobreak >nul
echo Dashboard lanzado en http://localhost:8599
endlocal
