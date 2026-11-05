@echo off
rem Shuttle route optimization with dummy data (data\sample_routing)
rem   run_sample.bat                  (all dates of the dummy data)
rem   run_sample.bat 2026-11-04       (specific dates)
rem   NOTE: keep this file ASCII only (Japanese text breaks cmd.exe)
chcp 65001 > nul
cd /d %~dp0
set PYTHONPATH=%~dp0src
if not exist data\sample_routing\attendance.csv (
    .venv\Scripts\python tools\make_dummy_routing.py --out data\sample_routing --seed 1
)
.venv\Scripts\python -m shuttle_opt.routing.main --data data\sample_routing --out output\sample %*
pause
