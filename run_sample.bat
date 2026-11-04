@echo off
rem 送迎ルート最適化（ダミーデータ: data\sample_routing）で動作確認
rem   run_sample.bat                      … ダミーデータの全日を計画
rem   run_sample.bat 2026-11-04           … 指定した日だけ
chcp 65001 > nul
cd /d %~dp0
set PYTHONPATH=%~dp0src
if not exist data\sample_routing\attendance.csv (
    .venv\Scripts\python tools\make_dummy_routing.py --out data\sample_routing --seed 1
)
.venv\Scripts\python -m shuttle_opt.routing.main --data data\sample_routing --out output\sample %*
pause
