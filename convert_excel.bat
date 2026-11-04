@echo off
rem ============================================================
rem  Excel（送迎テンプレート）→ data\real\ の CSV に変換
rem    使い方1: このファイルに Excel をドラッグ＆ドロップ
rem    使い方2: convert_excel.bat 送迎テンプレート.xlsm --facility 36.25,137.97
rem             （--facility は施設の緯度,経度。初回だけ指定すれば次回からは不要）
rem  ※ 氏名・備考の列は読み込みません
rem ============================================================
chcp 65001 > nul
cd /d %~dp0
if "%~1"=="" (
    echo Excel ファイルを、この convert_excel.bat にドラッグ＆ドロップしてください。
    pause
    exit /b 1
)
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.routing.excel_import %*
pause
