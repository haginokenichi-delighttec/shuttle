# 送迎ルート最適化 (OR-Tools)

介護施設の1日分の送迎（迎え・送り）について、「誰が・どの車で・何時に・どの順で・誰を」乗せるかを
OR-Tools（Routing + CP-SAT）で決めます。

## セットアップ (Windows)

前提: Python 3.10 以上（`py` ランチャーまたは `python` が使えること）

1. `setup.bat` をダブルクリック → `.venv` 作成と OR-Tools などのインストール
2. `run_routing.bat` → `attendance.csv` にある全日を計画し、`output\routing\` に結果を出力
   （日付を指定する場合は `run_routing.bat 2026-11-09`、オプション付きは `run_routing.bat --early 45`）
3. `test.bat` → テスト実行

手動で行う場合:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
python -m shuttle_opt.routing.main --data data\sample_routing --out output\routing
rem 日付指定（複数可）: python -m shuttle_opt.routing.main 2026-11-09 2026-11-10
```

主なオプション: `--early 20`（迎えの早着許容・分）、`--late 10`（送りの出発遅れ許容・分）、
`--time-limit 30`（Stage B の計算時間・秒）、`--matrix 移動時間.csv`（Google API 等で取得した移動時間キャッシュ）

## フォルダ構成

```
shuttle/
├─ data/
│  ├─ sample_routing/    ダミーデータ（匿名ID・架空座標）
│  └─ real/              実データ（匿名化済みのみ・gitignore）
├─ src/shuttle_opt/routing/
│  ├─ data.py            CSV 読み込み、当日分の職員・車・利用者、便（プラン×迎え/送り）の生成
│  ├─ travel_time.py     移動時間（直線距離×迂回係数÷平均速度 / キャッシュCSV）
│  ├─ route_gen.py       Stage A: 候補ルート生成（OR-Tools Routing）
│  ├─ assign.py          Stage B: 候補の選択と職員・車の割当（CP-SAT）
│  ├─ validate.py        ソルバーとは別ロジックで全ルールを再チェック
│  ├─ pipeline.py        Stage A → B の実行、職員別タイムライン
│  ├─ config.py          パラメータ
│  └─ main.py            CLI・結果出力
├─ tools/make_dummy_routing.py   ダミーデータ生成
├─ tests/                pytest
├─ docs/                 設計資料
├─ output/               結果
├─ requirements.txt
└─ setup.bat / run_routing.bat / test.bat
```

## 解き方

| 段階 | 内容 |
|---|---|
| Stage A | 便ごとに OR-Tools Routing を条件（台数・介助スキルの割り振り・所要時間の上限・初期解の作り方）を変えて繰り返し解き、候補ルートをためる |
| Stage B | CP-SAT で「どの候補ルートを何時に出すか」と「職員・車」を同時に決める。各利用者は各便でちょうど1回乗車、職員・車の時間重複なし、シフト内、介助スキル・定員・歩行器・所有車ルールを守る |
| 目的 | 走行時間＋ルート数＋優先度の低い職員の使用＋担当の偏り＋早着/出発遅れ（乗せられない利用者には大きなペナルティ） |

## 入力CSV

`data/sample_routing/`（`tools/make_dummy_routing.py` で再生成可）

| ファイル | 列 |
|---|---|
| facility.csv | lat, lng |
| staff.csv | staff_id, priority(大きいほど優先して使う), care_skill(1〜3) |
| shifts.csv | date, staff_id, start, end |
| cars.csv | car_id, capacity, walker_slots, owner_staff_id(空欄=施設車) |
| users.csv | user_id, lat, lng, care_need(1〜3), walker(0/1), seats, service_min |
| plans.csv | plan_id, arrive, depart |
| attendance.csv | date, user_id, plan_id |

## 出力

`output/routing/` に `routes_<日付>.csv`（ルート単位）、`stops_<日付>.csv`（停車地単位）、`timeline_<日付>.txt`（職員別）、複数日を計画したときは `summary.csv`（日ごとの結果一覧）。
乗せられない利用者がいる場合は、理由の目安とともに警告を出します（例: 介助レベル3に対応できる職員がその時間にいない）。

## 今後の拡張候補

- 実データ（xlsm）の匿名化・CSV変換スクリプト
- 最大乗車時間・同乗NG/希望などの現場ルール
- Google Routes API による移動時間キャッシュの作成
- Excel 出力・地図表示

## 個人情報の取り扱い

医療機関の情報を扱うため、**入力データには氏名・住所・病名などの個人情報を含めず、匿名ID（S01, U001 など）で処理**してください。
実データは `data/real/` に置く想定で、`.gitignore` により管理対象外にしています。ID と実名の対応表は本システムの外で管理してください。
