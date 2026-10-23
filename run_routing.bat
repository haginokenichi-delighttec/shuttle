@echo off
rem 送迎ルート最適化
rem   run_routing.bat                     … attendance.csv にある全日を計画
rem   run_routing.bat 2026-11-09          … 指定した日だけ（複数可）
rem   run_routing.bat --early 45          … 全日をオプション付きで
chcp 65001 > nul
cd /d %~dp0
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.routing.main --data data\sample_routing --out output\routing %*
pause
