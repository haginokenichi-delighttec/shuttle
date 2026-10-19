# 送迎シフト最適化 (OR-Tools)

OR-Tools の CP-SAT ソルバーで送迎ドライバーのシフト（日付 × 午前/午後便）を割り当てる最適化システムの雛形です。

## セットアップ (Windows)

前提: Python 3.10 以上（`py` ランチャーまたは `python` が使えること）

1. `setup.bat` をダブルクリック → `.venv` 作成と OR-Tools などのインストール
2. `run.bat` → サンプルデータで最適化を実行し、`output\` に結果を出力
3. `test.bat` → テスト実行

手動で行う場合:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
python -m shuttle_opt.main --data data\sample --out output
```

主なオプション: `--time-limit 60`（求解時間 秒）、`--max-consecutive 4`（最大連続勤務日数）

## フォルダ構成

```
shuttle/
├─ data/
│  └─ sample/            ダミーデータ（匿名ID）
│     ├─ drivers.csv        driver_id, max_shifts(期間内上限回数), wheelchair_ok(0/1)
│     ├─ demand.csv         date, slot(AM/PM), required(必要人数), required_wheelchair
│     └─ unavailability.csv driver_id, date, slot(AM/PM/ALL) … 勤務不可
├─ src/shuttle_opt/
│  ├─ data_loader.py     CSV 読み込み・検証
│  ├─ model.py           CP-SAT モデル（変数・制約・目的関数）
│  └─ main.py            CLI・結果出力
├─ tests/                pytest
├─ output/               結果（assignment.csv, shift_table.csv）
├─ requirements.txt
└─ setup.bat / run.bat / test.bat
```

## 現在のモデル

| 区分 | 内容 |
|---|---|
| 変数 | x[d,t,s] = ドライバー d が日付 t・便 s を担当するか (0/1) |
| ハード制約 | 勤務不可枠には割り当てない／期間内担当回数 ≤ max_shifts／連続勤務日数 ≤ 上限 |
| ソフト制約 | 必要人数・車椅子対応人数（不足はペナルティ付きで許容し、不足枠を出力） |
| 目的関数 | 不足人数 × 1000 + (担当回数の最大−最小) × 10 |

## 今後の拡張候補

- 車両（台数・定員・車椅子対応車）の割当
- 利用者ごとの送迎ルート作成（OR-Tools Routing / VRP with Time Windows）
- ドライバーの希望シフト・勤務時間上限・休憩
- Excel 入出力、画面（Streamlit 等）

## 個人情報の取り扱い

医療機関の情報を扱うため、**入力データには氏名・住所・病名などの個人情報を含めず、匿名ID（D01, U001 など）で処理**してください。
実データは `data/real/` に置く想定で、`.gitignore` により管理対象外にしています。ID と実名の対応表は本システムの外で管理してください。
