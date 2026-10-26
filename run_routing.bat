@echo off
rem Plan shuttle routes
rem   run_routing.bat                 (all dates in attendance.csv)
rem   run_routing.bat 2026-11-09      (specific dates)
rem   run_routing.bat --early 45      (all dates with options)
chcp 65001 > nul
cd /d %~dp0
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.routing.main --data data\sample_routing --out output\routing %*
pause
