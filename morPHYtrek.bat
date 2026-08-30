@echo off
:: ============================================================
::   morPHYtrek — The Voice Firewall (Portable, for the world)
::   The MOUTH. Listens, narrates (Piper), creates its OWN queue
::   folder (morphytrek_data\queue) next to itself. One queue.
::   Its own. Nothing else.
::   NOTHING hardcoded. Works from ANY folder on ANY Windows PC.
::   Title MORPHYTREK-PORTABLE so refresh NEVER kills the live mouth.
:: ============================================================
cd /d "%~dp0"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

:: --- Strip environment pollution FIRST (Hermes injects PYTHONPATH) ---
set PYTHONPATH=
set PYTHONHOME=
set VIRTUAL_ENV=

:: --- Python: the bundled runtime, ALWAYS. No interpreter swaps. ---
set "PY=%ROOT%\runtime\python.exe"
if not exist "%PY%" set "PY=%ROOT%\python.exe"
if not exist "%PY%" set "PY=python"

:: --- Dependency by ADDRESS: runtime python points at the venv's
::     compiled CUDA torch. Same python, one mouth, torch by location. ---
if exist "%USERPROFILE%\Desktop\MorPHYvenv\Lib\site-packages" set "PYTHONPATH=%USERPROFILE%\Desktop\MorPHYvenv\Lib\site-packages"

:: --- DEPENDENCY CHECK (first-run installer, standard behavior) ---
::     For each module: if the bundled python can import it, do nothing.
::     If it can't, install it. If pip itself is missing, bootstrap via get-pip.py.
::     Skips silently when everything is already present (normal boot).
::     Modules mirror the LIVE morPHYtrek.py auto-install list exactly.
echo Checking dependencies...
"%PY%" -c "import sounddevice"  2>nul || "%PY%" "%ROOT%\get-pip.py" --quiet 2>nul & "%PY%" -m pip install --quiet --disable-pip-version-check sounddevice  2>nul
"%PY%" -c "import faster_whisper" 2>nul || "%PY%" -m pip install --quiet --disable-pip-version-check faster_whisper 2>nul
"%PY%" -c "import pyperclip"   2>nul || "%PY%" -m pip install --quiet --disable-pip-version-check pyperclip   2>nul
"%PY%" -c "import pyautogui"   2>nul || "%PY%" -m pip install --quiet --disable-pip-version-check pyautogui   2>nul
"%PY%" -c "import pynput"      2>nul || "%PY%" -m pip install --quiet --disable-pip-version-check pynput      2>nul
echo Dependencies OK.

:: --- REFRESH MORPHYTREK (this bundle only — title MORPHYTREK-PORTABLE,
::     NEVER matches the LIVE mouth whose title is exactly "morPHYtrek") ---
taskkill /F /FI "WINDOWTITLE eq MORPHYTREK-PORTABLE*" >nul 2>&1
timeout /t 1 >nul

:: --- Launch the mouth DIRECTLY in this window (visible, labelled).
::     If it crashes, the error stays on screen so you can read it.
::     No hidden windows. No flash-and-close. Ever.
title MORPHYTREK-PORTABLE
"%PY%" "%ROOT%\morPHYtrek.py"

:: --- If we reach here, morPHYtrek exited. Keep the window open on crash
::     so the error is readable. Normal shutdown (Ctrl+C / window close)
::     also lands here.
if errorlevel 1 (
    echo.
    echo ============================================
    echo   morPHYtrek STOPPED with an error (code %errorlevel%)
    echo   The error is above. Window stays open.
    echo ============================================
    pause
)
exit
