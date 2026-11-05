@echo off
rem ============================================================
rem  Create the name list for the viewer (data\real\names.csv)
rem    Usage: drag and drop the Excel file (shuttle template) onto this file
rem  The output contains names. Do not share it outside.
rem  NOTE: keep this file ASCII only (Japanese text breaks cmd.exe)
rem ============================================================
chcp 65001 > nul
cd /d %~dp0
if "%~1"=="" (
    echo Drag and drop the Excel file onto export_names.bat.
    pause
    exit /b 1
)
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.routing.export_names %*
pause
