@echo off
rem ============================================================
rem  Setup (Windows)
rem  - create virtual env .venv and install requirements.txt
rem  - create dummy data in data\sample_routing
rem  NOTE: keep this file ASCII only (Japanese text breaks cmd.exe)
rem ============================================================
chcp 65001 > nul
cd /d %~dp0

where py > nul 2>&1
if %errorlevel%==0 (
    set PY=py -3
) else (
    set PY=python
)

if not exist .venv (
    echo [1/4] Creating virtual environment .venv ...
    %PY% -m venv .venv || goto :error
) else (
    echo [1/4] Using existing .venv
)

echo [2/4] Upgrading pip ...
.venv\Scripts\python -m pip install --upgrade pip || goto :error

echo [3/4] Installing packages ...
.venv\Scripts\python -m pip install -r requirements.txt || goto :error

echo [4/4] Creating dummy data for testing (data\sample_routing) ...
.venv\Scripts\python tools\make_dummy_routing.py --out data\sample_routing --seed 1 || goto :error

echo.
echo Setup completed. Check:
.venv\Scripts\python -c "import ortools; print('OR-Tools', ortools.__version__)"
echo.
echo How to use:
echo   1. Drag and drop the Excel template onto convert_excel.bat  (Excel to CSV in data\real)
echo   2. Run run_routing.bat  (results in output\routing)
echo   Dummy data: run_sample.bat   /   Tests: test.bat
pause
exit /b 0

:error
echo.
echo [ERROR] Setup failed. Please check that Python 3.10 or later is installed.
pause
exit /b 1
