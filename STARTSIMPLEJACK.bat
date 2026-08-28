@echo off
title STARTSIMPLEJACK - launcher
:: ============================================================
::   STARTSIMPLEJACK.bat — MorPHYes Mastery (Portable)
::   Flat code: no parenthesized blocks, no labels inside blocks,
::   no delayed expansion. One double-click = full suite on ANY
::   Windows PC:
::   1. HUB engine on 8123   (starts it if missing)
::   2. DISPATCH stack runner
::   3. BRAIN simplejack.py on 8797
::   4. MOUTH morPHYtrek.py (creates the queue = voice switch)
::   5. AILA.exe native Tauri window
::   Kills NOTHING that is already running. Skips what is up.
:: ============================================================
cd /d "%~dp0"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

:: --- Strip environment pollution FIRST (agents inject PYTHONPATH) ---
set PYTHONPATH=
set PYTHONHOME=
set VIRTUAL_ENV=

:: --- Find Python: bundled runtime > beside us > PATH ---
set "PY=%ROOT%\runtime\python.exe"
if not exist "%PY%" set "PY=%ROOT%\python.exe"
if not exist "%PY%" set "PY=python"

:: --- Hub python: the bundled runtime (INSTALL.bat keeps its deps fed) ---
set "HUB_PY=%ROOT%\runtime\python.exe"
if not exist "%HUB_PY%" set "HUB_PY=%PY%"

echo.
echo   STARTSIMPLEJACK - MorPHYes Mastery portable
echo   Root: %ROOT%
echo.

:: ================= STEP 1: HUB ENGINE (8123) =================
netstat -ano 2>nul | findstr ":8123 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto hub_up

echo   [1/5] HUB down - starting engine on 8123 ...
start "MODEL HUB :8123 (engine)" /min "%HUB_PY%" "%ROOT%\modelhub\model_loader.py"

set /a HUB_WAIT=0

:hub_poll
ping -n 2 127.0.0.1 >nul 2>&1
netstat -ano 2>nul | findstr ":8123 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto hub_up
set /a HUB_WAIT+=1
if %HUB_WAIT% LSS 15 goto hub_poll

echo   [1/5] *** HUB FAILED TO BIND 8123 after 15 tries ***
echo         SimpleJack will have NO ENGINE. Check the MODEL HUB window.
goto step2

:hub_up
echo   [1/5] HUB engine up on 8123.

:: ================= STEP 2: DISPATCH =================
:step2
tasklist /FI "WINDOWTITLE eq DISPATCH - stack runner*" 2>nul | findstr /I "cmd.exe" >nul
if not errorlevel 1 goto disp_up
echo   [2/5] DISPATCH down - starting stack runner ...
start "DISPATCH - stack runner" "%PY%" "%ROOT%\dispatch\dispatch.py"
goto disp_wait

:disp_up
echo   [2/5] DISPATCH already running - skip.

:disp_wait
ping -n 2 127.0.0.1 >nul 2>&1

:: ================= STEP 3: BRAIN (8797) =================
netstat -ano 2>nul | findstr ":8797 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto brain_refresh

echo   [3/5] BRAIN down - starting simplejack.py on 8797 ...
goto brain_start

:brain_refresh
echo   [3/5] BRAIN already on 8797 - restarting fresh ...
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr ":8797 " ^| findstr "LISTENING"') do taskkill /F /T /PID %%P >nul 2>&1
ping -n 2 127.0.0.1 >nul 2>&1

:brain_start
start "SIMPLEJACK :8797 (brain)" "%PY%" "%ROOT%\simplejack.py"
ping -n 4 127.0.0.1 >nul 2>&1
netstat -ano 2>nul | findstr ":8797 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 goto brain_fail
echo   [3/5] BRAIN up on 8797.
goto step4

:brain_fail
echo   [3/5] *** BRAIN did NOT bind 8797 - look at the SIMPLEJACK window ***

:: ================= STEP 4: MOUTH (morPHYtrek) =================
:step4
tasklist /FI "WINDOWTITLE eq MORPHYTREK-PORTABLE*" 2>nul | findstr /I "python" >nul
if not errorlevel 1 goto mouth_up
echo   [4/5] MOUTH down - starting morPHYtrek (voice) ...
start "MORPHYTREK-PORTABLE (voice)" "%PY%" "%ROOT%\morPHYtrek.py"
goto mouth_done

:mouth_up
echo   [4/5] MOUTH already running - skip.

:mouth_done
ping -n 2 127.0.0.1 >nul 2>&1

:: ================= STEP 5: AILA.exe (Tauri window) =================
tasklist /FI "IMAGENAME eq AILA.exe" 2>nul | findstr /I "AILA.exe" >nul
if not errorlevel 1 goto aila_up
echo   [5/5] Starting AILA.exe (Tauri window) ...
start "" "%ROOT%\AILA.exe"
goto done

:aila_up
echo   [5/5] AILA.exe already running - skip.

:done
echo.
echo ============================================
echo   SimpleJack is LIVE (Portable Native App)
echo   Hub 8123 / Brain 8797 / Voice / Tauri
echo ============================================
echo.
echo   This window closes in 10 seconds ...
timeout /t 10 >nul
exit
