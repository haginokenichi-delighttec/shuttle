@echo off
rem 送迎ルート最適化（実データ: data\real）
rem   run_routing.bat                     … attendance.csv にある全日を計画
rem   run_routing.bat 2026-11-09          … 指定した日だけ（複数可）
rem   run_routing.bat --early 45          … 全日をオプション付きで
rem   ※ 先に convert_excel.bat で Excel を CSV に変換しておくこと
chcp 65001 > nul
cd /d %~dp0
if not exist data\real\attendance.csv (
    echo data\real に CSV がありません。先に convert_excel.bat で Excel を変換してください。
    pause
    exit /b 1
)
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.routing.main --data data\real --out output\routing %*
pause
