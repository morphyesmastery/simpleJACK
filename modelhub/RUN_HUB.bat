@echo off
:: ============================================================
::   RUN_HUB.bat — start the MorPHYes Model Hub engine (8123)
::   Hub python: the bundled runtime (INSTALL.bat keeps its
::   deps — fastapi, uvicorn — fed into it).
:: ============================================================
title MORPHYES-MODELHUB
cd /d "%~dp0.."
set "ROOT=%cd%"
set PYTHONPATH=
set PYTHONHOME=
set VIRTUAL_ENV=

set "HUB_PY=%ROOT%\runtime\python.exe"

echo   Starting Model Hub on 8123 with %HUB_PY% ...
"%HUB_PY%" "%ROOT%\modelhub\model_loader.py"
pause
