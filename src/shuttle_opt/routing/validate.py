"""解の検証. ソルバーとは独立に、移動時間を計算し直してルールを確認する.

戻り値はエラーメッセージのリスト（空なら問題なし）。
"""
from __future__ import annotations

from collections import defaultdict

from .config import RoutingConfig
from .data import DayProblem, home_id, min_to_hm
from .route_gen import PlannedRoute
from . import lunch
from .travel_time import DEPOT, TravelTimeProvider


def validate(day: DayProblem, planned: list[PlannedRoute], provider: TravelTimeProvider,
             cfg: RoutingConfig, allow_dropped: dict[str, list[str]] | None = None) -> list[str]:
    errs: list[str] = []
    waves = {w.wave_id: w for w in day.waves}
    owners = {c.owner for c in day.cars.values() if c.owner}
    allow_dropped = allow_dropped or {}

    # 1. 各便で全員がちょうど1回乗っている（乗せられないと報告済みの人を除く）
    seen: dict[str, list[str]] = defaultdict(list)
    for p in planned:
        for wid, uids in p.route.segments():
            seen[wid] += uids
    for w in day.waves:
        for u in w.user_ids:
            expect = 0 if u in allow_dropped.get(w.wave_id, []) else 1
            if seen[w.wave_id].count(u) != expect:
                errs.append(f"{w.wave_id}: {u} の乗車回数が {seen[w.wave_id].count(u)} 回")
        for u in set(seen[w.wave_id]) - set(w.user_ids):
            errs.append(f"{w.wave_id}: 対象外の利用者 {u} が乗っている")

    busy = defaultdict(list)
    for p in planned:
        r, rid = p.route, p.route.route_id
        s, c = day.staff[p.staff_id], day.cars[p.car_id]

        # 2. 定員・歩行器・車椅子・介助・同乗NG（連結ルートは送りと迎えで別々に乗る）
        for wid, uids in r.segments():
            us = [day.users[u] for u in uids]
            if sum(u.seats for u in us) > c.capacity:
                errs.append(f"{rid}: 定員超過（{c.car_id}）")
            if sum(u.wheelchair for u in us) > c.wheelchair_slots:
                errs.append(f"{rid}: 車椅子の積載超過（{c.car_id}）")
            if sum(u.walker for u in us) > c.walker_slots:
                errs.append(f"{rid}: 歩行器の積載超過（{c.car_id}）")
            if max(u.care_need for u in us) > s.care_skill:
                errs.append(f"{rid}: 介助スキル不足（{s.staff_id}）")
            if not day.ng_ok(uids):
                errs.append(f"{rid}: 同乗NGの利用者が同じ車に乗っている")

        # 3. 所有車ルール
        if c.owner and c.owner != s.staff_id:
            errs.append(f"{rid}: {c.car_id} は {c.owner} の所有車")
        if s.staff_id in owners and c.owner != s.staff_id:
            errs.append(f"{rid}: {s.staff_id} は自分の車以外を運転している")

        # 4. 時刻を計算し直す（連結ルートは、送りの後に空車で待ってもよい）
        t, prev = p.start, r.start_pt
        n_first = r.n_first if r.is_chain else len(r.stops)
        for i, (uid, eta) in enumerate(zip(r.stops, p.eta)):
            t += provider.minutes(prev, uid)
            if r.is_chain and i == n_first:
                if eta < t:
                    errs.append(f"{rid}: 迎えの1人目 {uid} に {min_to_hm(eta)} には着けない（計算値 {min_to_hm(t)}）")
                t = eta
            elif eta != t:
                errs.append(f"{rid}: {uid} の到着予定 {min_to_hm(eta)} が計算値 {min_to_hm(t)} と違う")
            t += day.users[uid].service_min
            prev = uid
        t += provider.minutes(prev, r.end_pt)
        if p.end != t:
            errs.append(f"{rid}: 到着 {min_to_hm(p.end)} が計算値 {min_to_hm(t)} と違う")

        # 4b. 自宅発着は、その職員の直行／直帰のときだけ
        if r.start_pt != DEPOT and not (s.direct_start and r.start_pt == home_id(s.staff_id) and r.kind == "pickup"):
            errs.append(f"{rid}: 出発地 {r.start_pt} は {s.staff_id} の直行として不正")
        if r.end_pt != DEPOT and not (s.direct_end and r.end_pt == home_id(s.staff_id) and r.kind == "dropoff"):
            errs.append(f"{rid}: 到着地 {r.end_pt} は {s.staff_id} の直帰として不正")

        # 5. 便の時刻
        w1 = waves[r.wave_id]
        w_pick = waves[r.wave2] if r.is_chain else (w1 if w1.kind == "pickup" else None)
        w_drop = w1 if w1.kind == "dropoff" else None
        if w_pick and not (w_pick.time - cfg.early_eps <= p.end <= w_pick.time):
            errs.append(f"{rid}: 施設到着 {min_to_hm(p.end)} が時間窓外")
        if w_drop and not (w_drop.time <= p.start <= w_drop.time + cfg.late_eps):
            errs.append(f"{rid}: 施設出発 {min_to_hm(p.start)} が時間窓外")
        if r.is_chain:
            a, b = p.idle
            if not (0 <= b - a <= cfg.chain_max_wait):
                errs.append(f"{rid}: 送りと迎えの間の待機 {b - a} 分が範囲外")

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

    # 8. 直行・直帰
    by_staff: dict[str, list[PlannedRoute]] = defaultdict(list)
    for p in planned:
        by_staff[p.staff_id].append(p)
    for sid, ps in by_staff.items():
        st = day.staff[sid]
        ps.sort(key=lambda p: p.start)
        first, last = ps[0], ps[-1]
        if st.direct_start and first.route.kind == "pickup" and first.route.start_pt == DEPOT:
            errs.append(f"{sid}: 直行なのに最初の迎え {first.route.route_id} が施設発")
        if st.direct_end and last.route.kind == "dropoff" and last.route.end_pt == DEPOT:
            errs.append(f"{sid}: 直帰なのに最後の送り {last.route.route_id} が施設着")
        for p in ps[1:]:
            if p.route.start_pt != DEPOT:
                errs.append(f"{sid}: 自宅発ルート {p.route.route_id} が最初のルートではない")
        for p in ps[:-1]:
            if p.route.end_pt != DEPOT:
                errs.append(f"{sid}: 自宅着ルート {p.route.route_id} が最後のルートではない")

    # 9. 昼休み
    for sid, br in lunch.breaks(day, planned, cfg).items():
        if br is None:
            errs.append(f"{sid}: {cfg.lunch_start}〜{cfg.lunch_end} に連続{cfg.lunch_min}分の休憩が取れない")
    return errs
