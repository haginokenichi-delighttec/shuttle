"""解の検証. ソルバーとは独立に、移動時間を計算し直してルールを確認する.

戻り値はエラーメッセージのリスト（空なら問題なし）。
"""
from __future__ import annotations

from collections import defaultdict

from .config import RoutingConfig
from .data import DayProblem, min_to_hm
from .route_gen import PlannedRoute
from .travel_time import DEPOT, TravelTimeProvider


def validate(day: DayProblem, planned: list[PlannedRoute], provider: TravelTimeProvider,
             cfg: RoutingConfig, allow_dropped: dict[str, list[str]] | None = None) -> list[str]:
    errs: list[str] = []
    waves = {w.wave_id: w for w in day.waves}
    owners = {c.owner for c in day.cars.values() if c.owner}
    allow_dropped = allow_dropped or {}

    # 1. 各便で全員がちょうど1回乗っている（乗せられないと報告済みの人を除く）
    for w in day.waves:
        seen = [u for p in planned if p.route.wave_id == w.wave_id for u in p.route.stops]
        for u in w.user_ids:
            expect = 0 if u in allow_dropped.get(w.wave_id, []) else 1
            if seen.count(u) != expect:
                errs.append(f"{w.wave_id}: {u} の乗車回数が {seen.count(u)} 回")
        for u in set(seen) - set(w.user_ids):
            errs.append(f"{w.wave_id}: 対象外の利用者 {u} が乗っている")

    busy = defaultdict(list)
    for p in planned:
        r, rid = p.route, p.route.route_id
        s, c, w = day.staff[p.staff_id], day.cars[p.car_id], waves[r.wave_id]
        us = [day.users[u] for u in r.stops]

        # 2. 定員・歩行器・介助
        if sum(u.seats for u in us) > c.capacity:
            errs.append(f"{rid}: 定員超過（{c.car_id}）")
        if sum(u.wheelchair for u in us) > c.wheelchair_slots:
            errs.append(f"{rid}: 車椅子の積載超過（{c.car_id}）")
        if sum(u.walker for u in us) > c.walker_slots:
            errs.append(f"{rid}: 歩行器の積載超過（{c.car_id}）")
        if max(u.care_need for u in us) > s.care_skill:
            errs.append(f"{rid}: 介助スキル不足（{s.staff_id}）")

        # 3. 所有車ルール
        if c.owner and c.owner != s.staff_id:
            errs.append(f"{rid}: {c.car_id} は {c.owner} の所有車")
        if s.staff_id in owners and c.owner != s.staff_id:
            errs.append(f"{rid}: {s.staff_id} は自分の車以外を運転している")

        # 4. 時刻を計算し直す
        t, prev = p.start, DEPOT
        for u, eta in zip(us, p.eta):
            t += provider.minutes(prev, u.user_id)
            if eta != t:
                errs.append(f"{rid}: {u.user_id} の到着予定 {min_to_hm(eta)} が計算値 {min_to_hm(t)} と違う")
            t += u.service_min
            prev = u.user_id
        t += provider.minutes(prev, DEPOT)
        if p.end != t:
            errs.append(f"{rid}: 帰着 {min_to_hm(p.end)} が計算値 {min_to_hm(t)} と違う")

        # 5. 便の時刻
        if w.kind == "pickup" and not (w.time - cfg.early_eps <= p.end <= w.time):
            errs.append(f"{rid}: 施設到着 {min_to_hm(p.end)} が時間窓外")
        if w.kind == "dropoff" and not (w.time <= p.start <= w.time + cfg.late_eps):
            errs.append(f"{rid}: 施設出発 {min_to_hm(p.start)} が時間窓外")

        # 6. シフト
        if not (s.start <= p.start and p.end <= s.end):
            errs.append(f"{rid}: {s.staff_id} のシフト外")

        busy["職員", s.staff_id].append((p.start, p.end, rid))
        busy["車", c.car_id].append((p.start, p.end, rid))

    # 7. 職員・車の時間重複（準備時間込み）
    for key, iv in busy.items():
        iv.sort()
        for (s1, e1, r1), (s2, e2, r2) in zip(iv, iv[1:]):
            if e1 + cfg.turnaround_min > s2:
                errs.append(f"{key[0]} {key[1]}: {r1} と {r2} の時間が重なる")
    return errs
