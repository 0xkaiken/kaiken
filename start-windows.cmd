@echo off
setlocal
chcp 65001 >nul 2>&1
title KAIKEN — Mission Control
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not defined PLINY_AGENT_RUNTIME set "PLINY_AGENT_RUNTIME=codex"
if not defined PLINY_WORKSHOP set "PLINY_WORKSHOP=%~dp0state\workshop"

python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    python -X utf8 -u "%~dp0install_windows.py" --run --restart
    goto finished
)

for %%V in (-3 -3.13 -3.12 -3.11) do (
    py %%V -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
    if not errorlevel 1 (
        py %%V -X utf8 -u "%~dp0install_windows.py" --run --restart
        goto finished
    )
)

echo Python 3.11 or newer is required. Install Python and enable its PATH option.
set "PLINY_EXIT_CODE=1"
goto maybe_pause

:finished
set "PLINY_EXIT_CODE=%errorlevel%"

:maybe_pause
if not "%PLINY_EXIT_CODE%"=="0" (
    if not defined CI pause
)
exit /b %PLINY_EXIT_CODE%
