@echo off
rem ============================================================
rem  送迎表ビューア用の氏名名簿（data\real\names.csv）を作る
rem    使い方: このファイルに Excel（送迎テンプレート）をドラッグ＆ドロップ
rem  ※ 氏名を含むファイルです。外部に出さないでください
rem ============================================================
chcp 65001 > nul
cd /d %~dp0
if "%~1"=="" (
    echo Excel ファイルを、この export_names.bat にドラッグ＆ドロップしてください。
    pause
    exit /b 1
)
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.routing.export_names %*
pause
