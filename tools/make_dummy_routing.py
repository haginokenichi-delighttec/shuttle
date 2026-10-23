"""送迎ルート最適化用のダミーデータを生成する.

実在の人物・住所とは無関係な架空データ（匿名ID・乱数で作った座標）です。
規模: 職員10名 / 車6台（うち2台は職員所有） / 登録利用者180名 / 当日40名 / プラン4

使い方:
    python tools/make_dummy_routing.py --out data/sample_routing --seed 1
"""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import pandas as pd

# 架空の施設位置（どこでもよい。乱数座標の中心として使うだけ）
FACILITY = (35.000, 135.000)
RADIUS_KM = 4.0   # 施設から半径4km程度（車で10〜15分圏）

DATES = ["2026-11-02", "2026-11-03", "2026-11-04"]

PLANS = [
    # plan_id, 到着, 出発, 当日の人数の目安（偏りあり）
    ("P1", "09:00", "12:00", 16),
    ("P2", "09:30", "15:30", 12),
    ("P3", "10:00", "16:00", 8),
    ("P4", "13:00", "16:30", 4),
]


def _random_point(rng: random.Random) -> tuple[float, float]:
    r = RADIUS_KM * math.sqrt(rng.random())
    th = rng.random() * 2 * math.pi
    dlat = (r * math.cos(th)) / 111.0
    dlng = (r * math.sin(th)) / (111.0 * math.cos(math.radians(FACILITY[0])))
    return round(FACILITY[0] + dlat, 5), round(FACILITY[1] + dlng, 5)


def make(out: Path, seed: int = 1, n_users: int = 180) -> None:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)

    pd.DataFrame([{"lat": FACILITY[0], "lng": FACILITY[1]}]).to_csv(out / "facility.csv", index=False)

    pd.DataFrame([{"plan_id": p, "arrive": a, "depart": d} for p, a, d, _ in PLANS]).to_csv(
        out / "plans.csv", index=False)

    # 職員: 優先度 1(なるべく使わない)〜5(優先して使う), 介助スキル 1〜3
    staff = [
        ("S01", 5, 3), ("S02", 5, 2), ("S03", 4, 3), ("S04", 4, 2), ("S05", 3, 2),
        ("S06", 3, 1), ("S07", 2, 3), ("S08", 1, 1), ("S09", 4, 2), ("S10", 2, 1),
    ]
    pd.DataFrame(staff, columns=["staff_id", "priority", "care_skill"]).to_csv(out / "staff.csv", index=False)

    # 車: 定員, 歩行器積載数, 所有者（空欄=施設車）
    cars = [
        ("CAR1", 8, 2, ""), ("CAR2", 7, 1, ""), ("CAR3", 7, 1, ""),
        ("CAR4", 5, 0, ""), ("CAR5", 4, 0, "S09"), ("CAR6", 4, 1, "S10"),
    ]
    pd.DataFrame(cars, columns=["car_id", "capacity", "walker_slots", "owner_staff_id"]).to_csv(
        out / "cars.csv", index=False)

    # シフト（パート勤務も混ぜる）
    patterns = [("07:30", "16:30"), ("08:00", "17:00"), ("08:00", "12:30"),
                ("12:00", "18:00"), ("08:30", "17:30")]
    rows = []
    for d in DATES:
        for sid, *_ in staff:
            if rng.random() < 0.1:   # たまに休み
                continue
            s, e = rng.choice(patterns[:2]) if rng.random() < 0.7 else rng.choice(patterns)
            rows.append({"date": d, "staff_id": sid, "start": s, "end": e})
    pd.DataFrame(rows).to_csv(out / "shifts.csv", index=False)

    # 登録利用者
    users = []
    for i in range(1, n_users + 1):
        lat, lng = _random_point(rng)
        need = rng.choices([1, 2, 3], weights=[5, 3, 1])[0]
        walker = int(rng.random() < 0.15)
        users.append({
            "user_id": f"U{i:03d}", "lat": lat, "lng": lng,
            "care_need": need, "walker": walker, "seats": 1,
            "service_min": 3 + need,   # 介助が大きいほど乗降に時間がかかる
        })
    pd.DataFrame(users).to_csv(out / "users.csv", index=False)

    # 当日の利用
    rows = []
    ids = [u["user_id"] for u in users]
    for d in DATES:
        pool = rng.sample(ids, sum(n for *_, n in PLANS))
        k = 0
        for pid, _, _, n in PLANS:
            for uid in pool[k:k + n]:
                rows.append({"date": d, "user_id": uid, "plan_id": pid})
            k += n
    pd.DataFrame(rows).to_csv(out / "attendance.csv", index=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/sample_routing")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    make(Path(a.out), a.seed)
    print(f"ダミーデータを出力しました: {a.out}")
