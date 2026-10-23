"""パラメータ（時間はすべて分）."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RoutingConfig:
    # 移動時間（直線距離ベース）
    detour: float = 1.3          # 直線距離 → 道路距離の係数
    speed_kmh: float = 25.0      # 平均速度

    # 便の時間窓
    early_eps: int = 20          # 迎え: 到着時刻の何分前から着いてよいか
    late_eps: int = 10           # 送り: 出発時刻から何分遅れて出てよいか
    day_start: str = "07:30"     # これより前には施設を出ない

    # 運用
    turnaround_min: int = 10     # ルートの間に必要な準備時間（職員・車とも）
    max_route_min: int = 120     # 1ルートの所要時間の上限（施設を出てから戻るまで）

    # Stage A: 候補ルート生成
    vehicle_fixed_cost: int = 30           # ルートを1本増やすコスト（分換算）
    pool_time_limit_sec: float = 0.5       # VRP 1回あたりの計算時間上限
    short_route_limits: tuple[int, ...] = (20, 30, 45, 60)   # 所要時間を制限した候補も作る（分）
    neighbor_pairs: int = 3                # 各利用者について近所の何人と2人乗り候補を作るか

    # Stage B: 選択と割当
    w_priority: int = 10         # 優先度の低い職員を使うペナルティの重み
    w_max_load: int = 5          # 担当ルート数の最大値を抑える重み
    w_time_shift: int = 1        # 迎えの早着・送りの出発遅れ（1分あたり）
    drop_penalty: int = 100_000  # 利用者を乗せられないときのペナルティ
    assign_time_limit_sec: int = 30
    num_workers: int = 8         # CP-SAT の並列数
    start_step: int = 5          # 出発時刻の候補の刻み（分）
