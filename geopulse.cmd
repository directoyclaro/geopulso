@echo off
REM Launcher de GeoPulse (Windows). Uso: geopulse.cmd init-db | collect instagram | stats | dashboard ...
setlocal
set "PYTHONPATH=%~dp0src"
python -m geopulse.cli %*
endlocal
