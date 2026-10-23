"""送迎ルート最適化（1日分の迎え・送り）.

流れ:
    data.load_day()          当日の入力を読み込み、便(Wave)を作る
    travel_time              移動時間行列（直線距離ベース / 将来 Google API）
    route_gen.generate()     Stage A: 便ごとに OR-Tools Routing でルートを作る
    assign.assign()          Stage B: CP-SAT で各ルートに (職員, 車) を割り当てる
    validate.validate()      ソルバーとは別ロジックで解を検証する
"""
