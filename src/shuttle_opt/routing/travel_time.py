"""移動時間（分）の計算.

Phase 1: 直線距離（ハーバサイン）× 迂回係数 ÷ 平均速度
Phase 2/3: Google Routes API などで取得した行列をCSVにキャッシュして使う（CachedMatrixProvider）

どちらも「地点ID → 地点ID の分数」を返すだけなので、ソルバー側はそのまま使えます。
地点IDは利用者ID、施設は "DEPOT"。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

import pandas as pd

DEPOT = "DEPOT"


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class TravelTimeProvider(Protocol):
    def minutes(self, a: str, b: str) -> int: ...


class HaversineProvider:
    def __init__(self, points: dict[str, tuple[float, float]], detour: float = 1.3, speed_kmh: float = 25.0):
        self.points, self.detour, self.speed = points, detour, speed_kmh

    def minutes(self, a: str, b: str) -> int:
        if a == b:
            return 0
        (la, ga), (lb, gb) = self.points[a], self.points[b]
        km = haversine_km(la, ga, lb, gb) * self.detour
        return max(1, math.ceil(km / self.speed * 60))


class CachedMatrixProvider:
    """from_id,to_id,minutes のCSV（Google API の取得結果など）を使う.

    行列にない組み合わせは fallback（通常は HaversineProvider）で補う。
    """

    def __init__(self, csv_path: str | Path, fallback: TravelTimeProvider | None = None):
        df = pd.read_csv(csv_path, dtype={"from_id": str, "to_id": str})
        self.table = {(r.from_id, r.to_id): int(r.minutes) for r in df.itertuples(index=False)}
        self.fallback = fallback

    def minutes(self, a: str, b: str) -> int:
        if a == b:
            return 0
        if (a, b) in self.table:
            return self.table[a, b]
        if self.fallback is None:
            raise KeyError(f"移動時間がありません: {a} -> {b}")
        return self.fallback.minutes(a, b)


def build_matrix(ids: list[str], provider: TravelTimeProvider) -> list[list[int]]:
    """ids の順に並べた移動時間行列."""
    return [[provider.minutes(a, b) for b in ids] for a in ids]
