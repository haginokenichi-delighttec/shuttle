@echo off
rem Shuttle route optimization (real data: data\real)
rem   run_routing.bat                 (all dates in attendance.csv)
rem   run_routing.bat 2026-11-09      (specific dates)
rem   run_routing.bat --early 45      (all dates with options)
rem   Convert the Excel file with convert_excel.bat first.
rem   NOTE: keep this file ASCII only (Japanese text breaks cmd.exe)
chcp 65001 > nul
cd /d %~dp0
if not exist data\real\attendance.csv (
    echo [ERROR] No CSV in data\real. Convert the Excel file with convert_excel.bat first.
    pause
    exit /b 1
)
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.routing.main --data data\real --out output\routing %*
pause
