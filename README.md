# 送迎ルート最適化 (OR-Tools)

介護施設の1日分の送迎（迎え・送り）について、「誰が・どの車で・何時に・どの順で・誰を」乗せるかを
OR-Tools（Routing + CP-SAT）で決めます。結果は CSV で出力し、ルートごとに Google マップで開ける URL も付けます。

## セットアップ (Windows)

前提: Python 3.10 以上（`py` ランチャーまたは `python` が使えること）

1. `setup.bat` をダブルクリック → `.venv` 作成と OR-Tools などのインストール
2. `run_routing.bat` → `attendance.csv` にある全日を計画し、`output\routing\` に結果を出力
   （日付を指定する場合は `run_routing.bat 2026-11-09`、オプション付きは `run_routing.bat --early 45`）
3. `test.bat` → テスト実行

`run_routing.bat` は日付なしで動くので、ダブルクリックでも実行できます（1日あたり30〜40秒）。

手動で行う場合:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
python -m shuttle_opt.routing.main --data data\sample_routing --out output\routing
rem 日付指定（複数可）: python -m shuttle_opt.routing.main 2026-11-09 2026-11-10
```

### 計画のオプション（run_routing.bat の後ろに付ける）

| オプション | 意味 | 既定 |
|---|---|---|
| `2026-11-09 2026-11-10` | 計画する日（複数可）。省略時は attendance.csv の全日 | 全日 |
| `--early 20` | 迎え: 到着時刻の何分前から着いてよいか | 20 |
| `--late 10` | 送り: 出発時刻から何分遅れて出てよいか | 10 |
| `--time-limit 30` | Stage B（CP-SAT）の計算時間の上限（秒）。長くすると解が良くなることがある | 30 |
| `--matrix 表.csv` | 移動時間の表を指定（省略時は `<data>\travel_matrix.csv` があれば自動で使う） | 自動 |
| `--no-matrix` | 移動時間の表を使わず、直線距離から推計する | — |
| `--data フォルダ` / `--out フォルダ` | 入力・出力フォルダ | data\sample_routing / output\routing |

その他の細かい設定（準備時間、最大ルート時間、目的関数の重みなど）は `src\shuttle_opt\routing\config.py` にあります。
現在の制約条件の一覧は `docs\送迎最適化_制約条件一覧.md` を参照。

### PowerShell で実行する場合

この README のコマンドはコマンドプロンプト（cmd）用です。PowerShell では次の点が違います。

| | コマンドプロンプト（cmd） | PowerShell |
|---|---|---|
| バッチの実行 | `run_routing.bat` | `.\run_routing.bat`（先頭に `.\` が必要） |
| 環境変数の設定 | `set GOOGLE_MAPS_API_KEY=キー` | `$env:GOOGLE_MAPS_API_KEY = "キー"` |
| 仮想環境の有効化 | `.venv\Scripts\activate` | `.venv\Scripts\Activate.ps1` |
| PYTHONPATH の設定 | `set PYTHONPATH=src` | `$env:PYTHONPATH = "src"` |

```powershell
# 例: PowerShell で shuttle フォルダを開いて
.\run_routing.bat
.\run_routing.bat 2026-11-09 --early 45

$env:GOOGLE_MAPS_API_KEY = "取得したAPIキー"
.\fetch_matrix.bat --dry-run
.\fetch_matrix.bat
```

- PowerShell で `set GOOGLE_MAPS_API_KEY=...` と書くと、エラーにはならないが環境変数は設定されない（PowerShell の変数になるだけ）。
- `$env:` で設定した値は、その PowerShell ウィンドウを閉じると消える。
- `Activate.ps1` が「スクリプトの実行が無効」で止まる場合は、バッチ（`.\run_routing.bat` など）を使えば有効化は不要。

## フォルダ構成

```
shuttle/
├─ data/
│  ├─ sample_routing/    入力データ（匿名ID）。現在は実データ寄りの内容
│  │  ├─ *.csv              入力CSV（下表）
│  │  ├─ travel_matrix.csv        Google API で取得した移動時間の表（fetch_matrix.bat で作成）
│  │  ├─ travel_matrix_points.csv 取得時の座標（座標が変わった人を検出するため）
│  │  └─ _before_fix/       整形前の元データ
│  └─ real/              実データ（匿名化済みのみ・gitignore）
├─ src/shuttle_opt/routing/
│  ├─ data.py            CSV 読み込み、当日分の職員・車・利用者、便（プラン×迎え/送り）の生成
│  ├─ travel_time.py     移動時間（直線距離×迂回係数÷平均速度 / 移動時間の表）
│  ├─ route_gen.py       Stage A: 候補ルート生成（OR-Tools Routing）
│  ├─ assign.py          Stage B: 候補の選択と職員・車の割当（CP-SAT）
│  ├─ validate.py        ソルバーとは別ロジックで全ルールを再チェック
│  ├─ pipeline.py        Stage A → B の実行、職員別タイムライン
│  ├─ maps.py            Google マップの URL（ナビ用・確認用）
│  ├─ config.py          パラメータ
│  └─ main.py            CLI・結果出力・ログ
├─ tools/
│  ├─ make_dummy_routing.py    ダミーデータ生成
│  └─ fetch_google_matrix.py   Google Routes API で移動時間の表を作る
├─ tests/                pytest
├─ docs/                 設計資料・制約条件一覧
├─ output/               結果
├─ requirements.txt
└─ setup.bat / run_routing.bat / fetch_matrix.bat / test.bat
```

## 解き方

| 段階 | 内容 |
|---|---|
| Stage A | 便ごとに OR-Tools Routing を条件（台数・介助スキルの割り振り・所要時間の上限・初期解の作り方）を変えて繰り返し解き、候補ルートをためる |
| Stage B | CP-SAT で「どの候補ルートを何時に出すか」と「職員・車」を同時に決める。各利用者は各便でちょうど1回乗車、職員・車の時間重複なし、シフト内、介助スキル・定員・歩行器・車椅子・所有車ルールを守る |
| 目的 | 走行時間＋ルート数＋優先度の低い職員の使用＋担当の偏り＋早着/出発遅れ（乗せられない利用者には大きなペナルティ） |

## 入力CSV

`data/sample_routing/`

| ファイル | 列 |
|---|---|
| facility.csv | lat, lng（施設の位置。実座標が届くまでは利用者位置から算出した仮の値） |
| staff.csv | staff_id, priority(大きいほど優先して使う), care_skill(1〜3) |
| shifts.csv | date, staff_id, start, end |
| cars.csv | car_id, capacity, walker_slots, owner_staff_id(空欄=施設車), wheelchair_slots(車椅子の積載数。省略時0) |
| users.csv | user_id, lat, lng, care_need(1〜3), walker(0/1), wheelchair(0/1。省略時0), seats, service_min |
| plans.csv | plan_id, arrive, depart |
| attendance.csv | date, user_id, plan_id |

- 日付は `2026-11-09` でも `2026/11/9` でも読み込める。時刻は `9:00` / `09:00` どちらでもよい。
- care_need・walker・wheelchair・seats・service_min の空欄は、それぞれ 1・0・0・1・5分 として扱う。
- user_id の重複や、plans.csv にないプランIDは読み込み時にエラーで知らせる。

## 出力

`output/routing/` に出力します。

| ファイル | 中身 |
|---|---|
| `routes_<日付>.csv` | ルート単位（職員・車・便・施設出発/帰着・人数・歩行器・車椅子・順路・URL） |
| `stops_<日付>.csv` | 停車地単位（訪問順・到着予定時刻） |
| `timeline_<日付>.txt` | 職員ごとの1日の流れ |
| `summary.csv` | 日ごとの結果一覧（複数日を計画したとき） |
| `logs/run_<日時>.log` | 実行中に画面に出た内容すべて（設定値・警告・エラーを含む。デバッグ用） |

乗せられない利用者がいる場合は、画面とログに理由の目安とともに警告を出します（例: 介助レベル3に対応できる職員がその時間にいない）。

### Google マップで開く（APIキー不要）

`routes_<日付>.csv` の最後の2列に URL が入ります。

| 列 | 出発地 | 開いたときの動き |
|---|---|---|
| GoogleマップURL（ナビ用） | 指定なし（開いた端末の現在地） | スマホで開くと Google マップが起動し、訪問順どおりにすぐナビが始まる |
| 確認用URL | 施設 | ナビは始まらず、施設 → 利用者宅 → 施設 の経路全体を表示。PCでの事前確認用 |

- ナビ用は「開いた場所」が出発地になる。施設の駐車場で開く想定。
- 立ち寄り先の数には Google 側の上限がある（モバイルブラウザでは3か所程度）。定員の大きい車を使う場合は確認が必要。
- URL には利用者宅の座標が入る。出力ファイルは個人情報として扱うこと。

## 移動時間を Google の道路データにする（任意）

既定では、移動時間を直線距離 × 1.3 ÷ 時速25km で推計しています。
Google Routes API（computeRouteMatrix）で実際の道路の移動時間を取得すると、より正確になります。

コマンドプロンプト（cmd）の場合（PowerShell は上の「PowerShell で実行する場合」を参照）:

```bat
set GOOGLE_MAPS_API_KEY=取得したAPIキー
fetch_matrix.bat --dry-run   … 件数・リクエスト回数・所要時間の目安だけ表示（APIは呼ばない）
fetch_matrix.bat             … 取得して data\sample_routing\travel_matrix.csv を作る
fetch_matrix.bat --refresh   … 全件取り直し
```

- `travel_matrix.csv` があると `run_routing.bat` が自動で使う（画面とログに「移動時間: …」と表示）。
  表にない組は直線距離からの推計で補う。使わない場合は `--no-matrix`。
- **差分取得**：2回目以降は、新しい利用者・座標が変わった人（施設を含む）の分だけ取得する。
- **途中で止まっても大丈夫**：1リクエストごとにファイルへ追記している。もう一度実行すれば残りだけ取得する。
- **利用上限への対応**：Routes API の既定上限は 1分あたり 3,000 件。送信ペースを自動で 1分あたり 2,800 件までに抑える
  （上限を引き上げた場合は `--epm 件数` で変更）。全件（約1.9万件）で7分前後かかる。
  429（Quota exceeded）が出た場合は1分待って再試行し、それでも続く場合は止まる。1日あたりの上限の可能性もあるので
  Google Cloud コンソールの「割り当て」を確認する。
- `--traffic` を付けると交通状況を考慮する（TRAFFIC_AWARE）。料金区分が変わる可能性があるので料金表を確認すること。
- 事前に Google Cloud で **Routes API を有効化**しておくこと。料金は件数に応じてかかる。
- 利用者宅の座標を Google に送信するため、施設の規程を確認のうえ使うこと。

## トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| PowerShell で「'run_routing.bat' は…認識されません」 | 先頭に `.\` を付ける（`.\run_routing.bat`） |
| バッチ実行時に「'窶ｦ' は…認識されていません」など文字化けしたコマンドのエラー | バッチの中に日本語があると cmd が誤読する。バッチファイルには日本語を書かない（現在のバッチは英語のみ） |
| `GOOGLE_MAPS_API_KEY が設定されていません` | cmd は `set`、PowerShell は `$env:` で設定（上の表を参照） |
| Routes API エラー 429 | 上の「利用上限への対応」を参照 |
| 実行中に何も表示されないように見える | 1日あたり30〜40秒かかる。`output\routing\logs\` のログで進み具合を確認できる |

## 今後の拡張候補

- 実データ（xlsm）の匿名化・CSV変換スクリプト
- 最大乗車時間・同乗NG/希望などの現場ルール
- 乗せられない利用者の一覧ファイル出力
- Excel 出力・地図表示

## 個人情報の取り扱い

医療機関の情報を扱うため、**入力データには氏名・住所・病名などの個人情報を含めず、匿名ID（S0100, P0001 など）で処理**してください。
実データは `data/real/` に置く想定で、`.gitignore` により管理対象外にしています。ID と実名の対応表は本システムの外で管理してください。
現在 `data/sample_routing/` には利用者宅の実座標が入っているため、`data/real/` への移動を推奨します。
出力ファイル（URL を含む）や Google API の取得結果も個人情報として扱ってください。
