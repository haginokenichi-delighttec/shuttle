@echo off
chcp 65001 > nul
cd /d %~dp0
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.main --data data\sample --out output %*
pause
