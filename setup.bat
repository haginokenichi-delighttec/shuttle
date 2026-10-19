@echo off
rem ============================================================
rem  送迎シフト最適化 環境セットアップ (Windows)
rem  - .venv に仮想環境を作成し、requirements.txt をインストール
rem ============================================================
chcp 65001 > nul
cd /d %~dp0

where py > nul 2>&1
if %errorlevel%==0 (
    set PY=py -3
) else (
    set PY=python
)

if not exist .venv (
    echo [1/3] 仮想環境 .venv を作成しています...
    %PY% -m venv .venv || goto :error
) else (
    echo [1/3] 既存の .venv を使用します
)

echo [2/3] pip を更新しています...
.venv\Scripts\python -m pip install --upgrade pip || goto :error

echo [3/3] 依存パッケージをインストールしています...
.venv\Scripts\python -m pip install -r requirements.txt || goto :error

echo.
echo セットアップ完了。動作確認:
.venv\Scripts\python -c "import ortools; print('OR-Tools', ortools.__version__)"
echo.
echo 実行するには run.bat を、テストは test.bat を実行してください。
pause
exit /b 0

:error
echo.
echo [ERROR] セットアップに失敗しました。Python 3.10 以上がインストールされているか確認してください。
pause
exit /b 1
