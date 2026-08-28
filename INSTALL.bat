@echo off
:: ============================================================
::   MorPHYes Mastery — SIMPLEJACK INSTALLER (The $20 Folder)
::   ONE DOUBLE-CLICK. Naked laptop -> running simpleJACK.
::
::   Installs, in order, only what is missing:
::     1. dependencies   — straight into the bundled runtime
::     2. torch cu121     — GPU whisper. No CPU-only option exists.
::     3. trek + hub deps — faster-whisper, sounddevice, httpx, ...
::     4. Piper voice
::     5. Ollama          — silent install if absent (ollama.com)
::     6. The 3 Josies    — josie-4b-tools, josie-2b-tools, josie-9b-tools
::     7. The stack       — STARTSIMPLEJACK.bat: all five pieces
::
::   First run downloads are large. ~20 minutes on normal internet.
::   Every window stays open and labelled. Zero hidden processes.
:: ============================================================
title MORPHYES-INSTALLER
cd /d "%~dp0"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "RUNTIME_PY=%ROOT%\runtime\python.exe"
:: The bundled embedded runtime has no venv module - deps install
:: STRAIGHT INTO the runtime. One interpreter, one dependency set.
set "VENV_PY=%ROOT%\runtime\python.exe"

if not exist "%RUNTIME_PY%" (
    echo ============================================
    echo   ERROR: bundled runtime\python.exe missing.
    echo   This folder is incomplete. Re-extract the zip.
    echo ============================================
    pause
    exit /b 1
)

echo.
echo [1/7] Bundled runtime ready (dependencies install straight into it).

echo.
echo [2/7] Checking PyTorch cu121 (GPU)...
"%VENV_PY%" -c "import torch" 2>nul || (
    echo       Installing PyTorch cu121 - large download, please wait...
    "%VENV_PY%" -m pip install torch --index-url https://download.pytorch.org/whl/cu121 --quiet
)
"%VENV_PY%" -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 2)" 2>nul
if errorlevel 2 (
    echo       WARNING: CUDA not available on this machine. Torch installed,
    echo       but no NVIDIA GPU was found. The ear will run without it.
) else (
    echo       PyTorch cu121 ready. GPU confirmed.
)

echo.
echo [3/7] Checking ear + hub dependencies...
"%VENV_PY%" -m pip install --quiet --disable-pip-version-check faster-whisper sounddevice pyperclip pyautogui pynput numpy requests httpx fastapi uvicorn
echo       Dependencies ready.

echo.
echo [4/7] Checking Piper voice (the mouth)...
if not exist "%ROOT%\piper\piper.exe" (
    echo       Downloading Piper TTS engine ...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip' -OutFile '%ROOT%\piper.zip' -UseBasicParsing; Expand-Archive -Path '%ROOT%\piper.zip' -DestinationPath '%ROOT%\piper_tmp' -Force; Move-Item -Path '%ROOT%\piper_tmp\*' -Destination '%ROOT%\piper' -Force; Remove-Item '%ROOT%\piper.zip','%ROOT%\piper_tmp' -Recurse -Force } catch { exit 1 }"
    if not exist "%ROOT%\piper\piper.exe" (
        echo       WARNING: Piper download failed. The app runs, but voice
        echo       narration is silent. Re-run INSTALL.bat to retry.
    )
)
if not exist "%ROOT%\morphytrek_data\voices\en_GB-alba-medium.onnx" (
    echo       Downloading the Alba voice (~60 MB) ...
    if not exist "%ROOT%\morphytrek_data\voices" mkdir "%ROOT%\morphytrek_data\voices"
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alba/medium/en_GB-alba-medium.onnx' -OutFile '%ROOT%\morphytrek_data\voices\en_GB-alba-medium.onnx' -UseBasicParsing; Invoke-WebRequest -Uri 'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alba/medium/en_GB-alba-medium.onnx.json' -OutFile '%ROOT%\morphytrek_data\voices\en_GB-alba-medium.onnx.json' -UseBasicParsing } catch { exit 1 }"
    if not exist "%ROOT%\morphytrek_data\voices\en_GB-alba-medium.onnx" (
        echo       WARNING: voice download failed. Re-run INSTALL.bat to retry.
    )
) else (
    echo       Alba voice already present.
)

echo.
echo [5/7] Checking Ollama...
ollama --version 2>nul || (
    echo       Downloading Ollama installer from ollama.com ...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile '%ROOT%\OllamaSetup.exe' -UseBasicParsing } catch { exit 1 }"
    if exist "%ROOT%\OllamaSetup.exe" (
        echo       Installing Ollama silently...
        "%ROOT%\OllamaSetup.exe" /VERYSILENT /NORESTART
        timeout /t 10 /nobreak >nul
    ) else (
        echo       Could not download Ollama. Install it from ollama.com,
        echo       then run INSTALL.bat again.
        pause
        exit /b 1
    )
)
ollama --version 2>nul || (
    echo       Ollama installed but not on PATH yet. Close this window,
    echo       reopen a new one, run INSTALL.bat again.
    pause
    exit /b 1
)
echo       Ollama ready.

echo.
echo [6/7] Pulling the 3 Josies (first run: several GB, this is the 20 minutes)...
for %%M in (josie-4b-tools:latest josie-2b-tools:latest josie-9b-tools:latest) do (
    ollama pull %%M
)
echo       Josies ready.

echo.
echo [7/7] Launching the full suite via STARTSIMPLEJACK.bat ...
:: ONE ENTRY POINT: STARTSIMPLEJACK.bat starts all five pieces
:: (hub engine, dispatch, brain 8797, mouth, AILA.exe window).
call "%ROOT%\STARTSIMPLEJACK.bat"
timeout /t 8 /nobreak >nul
start "" "http://127.0.0.1:8797/"

echo.
echo ============================================
echo   simpleJACK IS LIVE.
echo   Add ONE key in the Model Hub to wake the
echo   cloud cards: ZenMux or OpenRouter
echo   (oxalpha is the suggested card).
echo   Hub:  http://127.0.0.1:8123
echo   Jack: http://127.0.0.1:8797
echo   No key? The Josies run fully local.
echo ============================================
echo.
echo This window can be closed. The stack keeps running.
pause
exit /b 0
