@echo off
rem ============================================================
rem  Excel (shuttle template) to CSV in data\real
rem    Usage 1: drag and drop the Excel file onto this file
rem    Usage 2: convert_excel.bat template.xlsm --facility 36.25,137.97
rem             (--facility = facility lat,lng. Needed only the first time)
rem  Name and note columns are not read.
rem  NOTE: keep this file ASCII only (Japanese text breaks cmd.exe)
rem ============================================================
chcp 65001 > nul
cd /d %~dp0
if "%~1"=="" (
    echo Drag and drop the Excel file onto convert_excel.bat.
    pause
    exit /b 1
)
set PYTHONPATH=%~dp0src
.venv\Scripts\python -m shuttle_opt.routing.excel_import %*
pause
