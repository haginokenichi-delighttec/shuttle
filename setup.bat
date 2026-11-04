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
    echo [1/4] 仮想環境 .venv を作成しています...
    %PY% -m venv .venv || goto :error
) else (
    echo [1/4] 既存の .venv を使用します
)

echo [2/4] pip を更新しています...
.venv\Scripts\python -m pip install --upgrade pip || goto :error

echo [3/4] 依存パッケージをインストールしています...
.venv\Scripts\python -m pip install -r requirements.txt || goto :error

echo [4/4] 動作確認用のダミーデータを作成しています...
.venv\Scripts\python tools\make_dummy_routing.py --out data\sample_routing --seed 1 || goto :error

echo.
echo セットアップ完了。動作確認:
.venv\Scripts\python -c "import ortools; print('OR-Tools', ortools.__version__)"
echo.
echo 使い方:
echo   1. convert_excel.bat に送迎テンプレートの Excel をドラッグ＆ドロップ（CSV に変換）
echo   2. run_routing.bat で送迎計画を作成（結果は output\routing）
echo   ダミーデータでの確認は run_sample.bat、テストは test.bat
pause
exit /b 0

:error
echo.
echo [ERROR] セットアップに失敗しました。Python 3.10 以上がインストールされているか確認してください。
pause
exit /b 1
