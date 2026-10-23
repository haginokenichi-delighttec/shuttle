"""Stage A: 候補ルート（ルートプール）の生成（OR-Tools Routing）.

便ごとに、条件（使う台数・車の大きさ・介助スキルの割り振り・初期解の作り方）を
変えて何度も VRP を解き、出てきたルートをすべて「候補」としてためる。
どの候補を採用し、誰がどの車で担当するかは Stage B（assign.py）が CP-SAT で同時に決める。

ルートの時刻はここでは決めない。「施設を出てから戻るまでの所要時間」と
「出発から各利用者宅までの経過時間」だけを持ち、出発時刻は Stage B が便の時間窓の中で決める。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .config import RoutingConfig
from .data import DayProblem, Wave, hm_to_min
from .travel_time import DEPOT, TravelTimeProvider, build_matrix

FSS = routing_enums_pb2.FirstSolutionStrategy


@dataclass
class Route:
    """候補ルート（時刻未確定）."""
    route_id: str
    wave_id: str
    kind: str
    stops: list[str]          # 利用者IDを訪問順に
    offsets: list[int]        # 施設を出てから各利用者宅に着くまでの分数
    duration: int             # 施設を出てから戻るまでの分数
    seats: int
    walkers: int
    max_need: int
    travel: int               # 走行時間（乗降を含まない）
    wheelchairs: int = 0      # 車椅子の人数
    start_lo: int = 0         # 施設を出てよい時刻の範囲（便の時間窓から計算）
    start_hi: int = 0


@dataclass
class PlannedRoute:
    """採用されたルート（時刻・担当確定）."""
    route: Route
    staff_id: str
    car_id: str
    start: int

    @property
    def end(self) -> int:
        return self.start + self.route.duration

    @property
    def eta(self) -> list[int]:
        return [self.start + o for o in self.route.offsets]


@dataclass
class Pool:
    routes: list[Route] = field(default_factory=list)
    uncoverable: dict[str, list[str]] = field(default_factory=dict)   # wave_id -> 誰も乗せられない利用者


def staff_available(kind: str, t: int, start: int, end: int) -> bool:
    if kind == "pickup":
        return start <= t - 10 and end >= t
    return start <= t and end >= t + 15


def _make_route(day: DayProblem, w: Wave, stops: list[str], provider: TravelTimeProvider,
                cfg: RoutingConfig) -> Route:
    """訪問順から所要時間などを計算する（途中で待たない前提）."""
    t, prev, travel, offsets = 0, DEPOT, 0, []
    for u in stops:
        d = provider.minutes(prev, u)
        t += d
        travel += d
        offsets.append(t)
        t += day.users[u].service_min
        prev = u
    d = provider.minutes(prev, DEPOT)
    t += d
    travel += d
    us = [day.users[u] for u in stops]
    if w.kind == "pickup":
        lo = max(hm_to_min(cfg.day_start), w.time - cfg.early_eps - t)
        hi = w.time - t
    else:
        lo, hi = w.time, w.time + cfg.late_eps
    return Route("", w.wave_id, w.kind, list(stops), offsets, t,
                 sum(u.seats for u in us), sum(u.walker for u in us),
                 max(u.care_need for u in us), travel,
                 sum(u.wheelchair for u in us), lo, hi)


def _solve_vrp(day: DayProblem, w: Wave, uids: list[str], vehicles: list[tuple[int, int, int, int]],
               provider: TravelTimeProvider, cfg: RoutingConfig, strategy,
               max_duration: int | None = None) -> list[list[str]]:
    """vehicles: [(定員, 歩行器枠, 車椅子枠, 介助スキル)]. 戻り値: 訪問順のリスト（乗せられない人は除外）."""
    ids = [DEPOT] + uids
    T = build_matrix(ids, provider)
    users = [day.users[u] for u in uids]
    service = [0] + [u.service_min for u in users]
    seats = [0] + [u.seats for u in users]
    walkers = [0] + [u.walker for u in users]
    chairs = [0] + [u.wheelchair for u in users]
    V = len(vehicles)

    mgr = pywrapcp.RoutingIndexManager(len(ids), V, 0)
    routing = pywrapcp.RoutingModel(mgr)

    def time_cb(i, j):
        a, b = mgr.IndexToNode(i), mgr.IndexToNode(j)
        return T[a][b] + service[a]

    t_idx = routing.RegisterTransitCallback(time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(t_idx)
    max_dur = (w.time - hm_to_min(cfg.day_start)) if w.kind == "pickup" else cfg.max_route_min
    max_dur = min(max_dur, cfg.max_route_min, max_duration or 10**9)
    routing.AddDimension(t_idx, 0, max(1, max_dur), True, "Time")
    routing.SetFixedCostOfAllVehicles(cfg.vehicle_fixed_cost)

    s_idx = routing.RegisterUnaryTransitCallback(lambda i: seats[mgr.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(s_idx, 0, [v[0] for v in vehicles], True, "Seat")
    w_idx = routing.RegisterUnaryTransitCallback(lambda i: walkers[mgr.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(w_idx, 0, [v[1] for v in vehicles], True, "Walker")
    c_idx = routing.RegisterUnaryTransitCallback(lambda i: chairs[mgr.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(c_idx, 0, [v[2] for v in vehicles], True, "Wheelchair")

    for n, u in enumerate(users, start=1):
        idx = mgr.NodeToIndex(n)
        banned = [v for v in range(V) if vehicles[v][3] < u.care_need]
        if len(banned) == V:
            routing.ActiveVar(idx).SetValue(0)
        elif banned:
            routing.VehicleVar(idx).RemoveValues(banned)
        routing.AddDisjunction([idx], cfg.drop_penalty)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = strategy
    params.time_limit.FromMilliseconds(int(cfg.pool_time_limit_sec * 1000))
    sol = routing.SolveWithParameters(params)
    if sol is None:
        return []
    out = []
    for v in range(V):
        idx = sol.Value(routing.NextVar(routing.Start(v)))
        stops = []
        while not routing.IsEnd(idx):
            stops.append(ids[mgr.IndexToNode(idx)])
            idx = sol.Value(routing.NextVar(idx))
        if stops:
            out.append(stops)
    return out


def generate_pool(day: DayProblem, provider: TravelTimeProvider, cfg: RoutingConfig) -> Pool:
    pool = Pool()
    seen: dict[tuple[str, frozenset], Route] = {}

    def add(w: Wave, stops: list[str]) -> None:
        r = _make_route(day, w, stops, provider, cfg)
        if r.start_lo > r.start_hi:
            return   # 時間窓に間に合わない
        key = (w.wave_id, frozenset(stops))
        if key not in seen or r.duration < seen[key].duration:
            seen[key] = r

    cars = sorted(day.cars.values(),
                  key=lambda c: (-c.capacity, -c.wheelchair_slots, -c.walker_slots, c.car_id))
    for w in day.waves:
        avail = [s for s in day.staff.values() if staff_available(w.kind, w.time, s.start, s.end)]
        if not avail or not cars:
            pool.uncoverable[w.wave_id] = list(w.user_ids)
            continue
        max_skill = max(s.care_skill for s in avail)
        max_walk = max(c.walker_slots for c in cars)
        max_chair = max(c.wheelchair_slots for c in cars)
        bad = [u for u in w.user_ids
               if day.users[u].care_need > max_skill
               or (day.users[u].walker and max_walk == 0)
               or (day.users[u].wheelchair and max_chair == 0)]
        if bad:
            pool.uncoverable[w.wave_id] = bad
        uids = [u for u in w.user_ids if u not in bad]
        if not uids:
            continue

        # 1人乗りルート（どんな日でも必ず候補がある状態にする）
        for u in uids:
            add(w, [u])

        skills = sorted((s.care_skill for s in avail), reverse=True)
        n_max = min(len(cars), len(avail))
        n_min = max(1, math.ceil(sum(day.users[u].seats for u in uids) / cars[0].capacity))
        for k in range(n_min, n_max + 1):
            sub = cars[:k]
            tier_sets = {
                "free": [3] * k,                                         # スキル無視
                "real": [skills[i] if i < len(skills) else 0 for i in range(k)],  # 実際の職員構成
                "one3": [max_skill] + [min(2, max_skill)] * (k - 1),    # 介助の重い人を1台に集約
            }
            for tiers in tier_sets.values():
                vehicles = [(c.capacity, c.walker_slots, c.wheelchair_slots, t) for c, t in zip(sub, tiers)]
                for strat in (FSS.PARALLEL_CHEAPEST_INSERTION, FSS.SAVINGS, FSS.PATH_CHEAPEST_ARC):
                    for stops in _solve_vrp(day, w, uids, vehicles, provider, cfg, strat):
                        add(w, stops)

        # 短いルート: 同じ車で近い便を続けて回れるように、所要時間を制限したルートも作る
        many = cars * 2
        for limit in cfg.short_route_limits:
            for tiers in ([3] * len(many), [skills[i % len(skills)] for i in range(len(many))]):
                vehicles = [(c.capacity, c.walker_slots, c.wheelchair_slots, t) for c, t in zip(many, tiers)]
                for strat in (FSS.PARALLEL_CHEAPEST_INSERTION, FSS.SAVINGS):
                    for stops in _solve_vrp(day, w, uids, vehicles, provider, cfg, strat, limit):
                        add(w, stops)

        # 近所の2人乗りルート
        for u in uids:
            near = sorted((x for x in uids if x != u), key=lambda x: provider.minutes(u, x))
            for x in near[:cfg.neighbor_pairs]:
                add(w, [u, x])

    count: dict[str, int] = {}
    for (wid, _), r in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[1].stops)):
        count[wid] = count.get(wid, 0) + 1
        r.route_id = f"{wid}#{count[wid]}"
        pool.routes.append(r)
    return pool
