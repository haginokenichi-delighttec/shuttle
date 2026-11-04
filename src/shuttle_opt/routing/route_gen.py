"""Stage A: 候補ルート（ルートプール）の生成（OR-Tools Routing）.

便ごとに、条件（使う台数・車の大きさ・介助スキルの割り振り・初期解の作り方）を
変えて何度も VRP を解き、出てきたルートをすべて「候補」としてためる。
どの候補を採用し、誰がどの車で担当するかは Stage B（assign.py）が CP-SAT で同時に決める。

ルートの時刻はここでは決めない。「施設を出てから戻るまでの所要時間」と
「出発から各利用者宅までの経過時間」だけを持ち、出発時刻は Stage B が便の時間窓の中で決める。
"""
from __future__ import annotations

import heapq
import math
from itertools import combinations, permutations
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .config import RoutingConfig
from .data import DayProblem, Wave, hm_to_min, home_id
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
    start_lo: int = 0         # 出発してよい時刻の範囲（便の時間窓から計算）
    start_hi: int = 0
    start_pt: str = DEPOT     # 出発地（施設 or 直行する職員の自宅 HOME_xxx）
    end_pt: str = DEPOT       # 到着地（施設 or 直帰する職員の自宅）
    staff_only: str | None = None   # 自宅発着ルートは、その職員しか担当できない
    # --- 送り→迎えの連結ルート（kind="chain"）用 ---
    # 送りで全員降ろしたあと施設に戻らず、そのまま次の迎えに向かう。
    # stops = 送りの利用者 n_first 人 + 迎えの利用者。迎えの施設到着は fixed_end に固定し、
    # 間の空き時間（空車で待機）は出発時刻によって変わる。
    wave2: str | None = None        # 迎え側の便
    n_first: int = 0                # 送り側の人数
    fixed_end: int | None = None    # 施設到着時刻（迎えの到着時刻）
    idle_from: int = 0              # 出発から「最後の送り＋次の迎え先への移動」が終わるまでの分数
    tail_len: int = 0               # 迎えの1人目の家に着いてから施設に着くまでの分数
    tail_offsets: list[int] = field(default_factory=list)   # 迎え側各宅への到着（fixed_end からの差、負）
    deadhead: int = 0               # 最後の送り先 → 最初の迎え先 の移動時間
    # 早着型の連結ルート: 空車で待つ必要がなければそのまま回り、施設に arrive_lo〜fixed_end の間に着く。
    # None なら定刻型（施設到着は fixed_end ちょうど）。
    arrive_lo: int | None = None
    drop_lo: int = 0                # 送り便の出発時刻（出発遅れの計算用）

    @property
    def is_chain(self) -> bool:
        return self.kind == "chain"

    def end_at(self, start: int) -> int:
        """出発時刻 start のときの到着時刻."""
        if not self.is_chain:
            return start + self.duration
        if self.arrive_lo is None:
            return self.fixed_end
        # 待たずに回ったときの到着（早すぎるときは arrive_lo まで待つ）
        return min(self.fixed_end, max(self.arrive_lo, start + self.idle_from + self.tail_len))

    def eta_at(self, start: int) -> list[int]:
        if not self.is_chain:
            return [start + o for o in self.offsets]
        end = self.end_at(start)
        return [start + o for o in self.offsets] + [end + o for o in self.tail_offsets]

    def idle_at(self, start: int) -> tuple[int, int] | None:
        """連結ルートの空車待機時間（開始, 終了）. 通常ルートは None."""
        if not self.is_chain:
            return None
        return start + self.idle_from, self.end_at(start) - self.tail_len

    def busy_parts(self, start: int, turnaround: int) -> list[tuple[int, int]]:
        """職員が手の離せない時間帯. 帰着後の準備時間を含む。連結ルートの空車待機は含まない."""
        if not self.is_chain:
            return [(start, start + self.duration + turnaround)]
        a, b = self.idle_at(start)
        return [(start, a), (b, self.end_at(start) + turnaround)]

    def segments(self) -> list[tuple[str, list[str]]]:
        """(便, 利用者) の組. 連結ルートは送りと迎えの2つ."""
        if not self.is_chain:
            return [(self.wave_id, self.stops)]
        return [(self.wave_id, self.stops[:self.n_first]), (self.wave2, self.stops[self.n_first:])]


@dataclass
class PlannedRoute:
    """採用されたルート（時刻・担当確定）."""
    route: Route
    staff_id: str
    car_id: str
    start: int

    @property
    def end(self) -> int:
        return self.route.end_at(self.start)

    @property
    def eta(self) -> list[int]:
        return self.route.eta_at(self.start)

    @property
    def idle(self) -> tuple[int, int] | None:
        return self.route.idle_at(self.start)


@dataclass
class Pool:
    routes: list[Route] = field(default_factory=list)
    uncoverable: dict[str, list[str]] = field(default_factory=dict)   # wave_id -> 誰も乗せられない利用者


def staff_available(kind: str, t: int, start: int, end: int, cfg: RoutingConfig | None = None) -> bool:
    """その便を担当できる勤務時間か（目安. 厳密なシフト内チェックは Stage B で行う）."""
    cfg = cfg or RoutingConfig()
    if kind == "pickup":
        return start <= t - cfg.staff_before_pickup and end >= t
    return start <= t and end >= t + cfg.staff_after_dropoff


def _make_route(day: DayProblem, w: Wave, stops: list[str], provider: TravelTimeProvider,
                cfg: RoutingConfig, start_pt: str = DEPOT, end_pt: str = DEPOT) -> Route:
    """訪問順から所要時間などを計算する（途中で待たない前提）."""
    t, prev, travel, offsets = 0, start_pt, 0, []
    for u in stops:
        d = provider.minutes(prev, u)
        t += d
        travel += d
        offsets.append(t)
        t += day.users[u].service_min
        prev = u
    d = provider.minutes(prev, end_pt)
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
                 sum(u.wheelchair for u in us), lo, hi, start_pt, end_pt)


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

    # 同乗NG: 両方とも乗るなら別の車
    pos = {u: n for n, u in enumerate(uids, start=1)}
    solver = routing.solver()
    for pair in day.ng_pairs:
        a, b = tuple(pair)
        if a in pos and b in pos:
            ia, ib = mgr.NodeToIndex(pos[a]), mgr.NodeToIndex(pos[b])
            same = solver.IsEqualVar(routing.VehicleVar(ia), routing.VehicleVar(ib))
            solver.Add(same * routing.ActiveVar(ia) * routing.ActiveVar(ib) == 0)

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
        if not day.ng_ok(stops):
            return   # 同乗NGの組を含む
        r = _make_route(day, w, stops, provider, cfg)
        if r.start_lo > r.start_hi:
            return   # 時間窓に間に合わない
        key = (w.wave_id, frozenset(stops))
        if key not in seen or r.duration < seen[key].duration:
            seen[key] = r

    cars = sorted(day.cars.values(),
                  key=lambda c: (-c.capacity, -c.wheelchair_slots, -c.walker_slots, c.car_id))
    for w in day.waves:
        avail = [s for s in day.staff.values() if staff_available(w.kind, w.time, s.start, s.work_end, cfg)]
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

    # 直行・直帰する職員用: 自宅発の迎えルート / 自宅着の送りルート
    variants: list[Route] = []
    waves = {w.wave_id: w for w in day.waves}
    for st in day.staff.values():
        if not st.home:
            continue
        hid = home_id(st.staff_id)
        for r in list(seen.values()):
            if r.max_need > st.care_skill:
                continue
            if r.kind == "pickup" and st.direct_start:
                sp, ep = hid, DEPOT
            elif r.kind == "dropoff" and st.direct_end:
                sp, ep = DEPOT, hid
            else:
                continue
            w = waves[r.wave_id]
            # 自宅から（自宅へ）だと順番を逆にした方が短いこともあるので、両方試す
            cands = [_make_route(day, w, order, provider, cfg, sp, ep)
                     for order in (r.stops, list(reversed(r.stops)))]
            v = min(cands, key=lambda x: x.duration)
            if v.start_lo > v.start_hi:
                continue
            if max(v.start_lo, st.start) > min(v.start_hi, st.work_end - v.duration):
                continue   # 勤務時間に収まらない
            v.staff_only = st.staff_id
            variants.append(v)

    count: dict[str, int] = {}
    for (wid, _), r in sorted(seen.items(), key=lambda kv: (kv[0][0], kv[1].stops)):
        count[wid] = count.get(wid, 0) + 1
        r.route_id = f"{wid}#{count[wid]}"
        pool.routes.append(r)
    for r in sorted(variants, key=lambda x: (x.wave_id, x.staff_only, x.stops)):
        count[r.wave_id] = count.get(r.wave_id, 0) + 1
        tag = "自宅発" if r.start_pt != DEPOT else "自宅着"
        r.route_id = f"{r.wave_id}#{count[r.wave_id]}({r.staff_only}{tag})"
        pool.routes.append(r)

    if cfg.chain_enabled:
        chains = build_chains(day, [r for r in pool.routes if r.staff_only is None], provider, cfg)
        for i, r in enumerate(chains, start=1):
            r.route_id = f"{r.wave_id}→{r.wave2}#{i}"
        pool.routes.extend(chains)
    return pool


def build_chains(day: DayProblem, routes: list[Route], provider: TravelTimeProvider,
                 cfg: RoutingConfig) -> list[Route]:
    """送り→迎えの連結ルート候補を作る.

    送りルートで全員降ろしたあと施設に戻らず、そのまま後の便の迎えに向かう。
    迎え側は、(1) 施設発で作った迎えルートに加えて、(2) 送りの最後の家から出発する前提で
    組み直した迎え（_pickups_from）も使う。
    施設に戻って出直すより移動が短くなる組（短くならなくても chain_min_saving 分の遠回りまでで、
    時間的に次の迎えに間に合うようになる組を含む）を、送りルート1本につき chain_per_route 組まで作る。
    1組につき「定刻型（施設に到着時刻ちょうど）」と「早着型（待たずに回り、最大 early_eps 分早く着く）」の
    2通りを候補にする。
    """
    waves = {w.wave_id: w for w in day.waves}
    drops = [r for r in routes if r.kind == "dropoff" and r.start_pt == DEPOT and r.end_pt == DEPOT]
    picks_by_wave: dict[str, list[Route]] = {}
    for r in routes:
        if r.kind == "pickup" and r.start_pt == DEPOT and r.end_pt == DEPOT:
            picks_by_wave.setdefault(r.wave_id, []).append(r)
    from_cache: dict[tuple[str, str], list[Route]] = {}
    out: list[Route] = []
    for d in drops:
        wd = waves[d.wave_id]
        last = d.stops[-1]
        back = provider.minutes(last, DEPOT)
        d_body = d.duration - back           # 施設を出て最後の人を降ろし終えるまで
        cands, seen_keys = [], set()
        for wp in day.waves:
            if wp.kind != "pickup" or wp.time <= wd.time:
                continue
            key = (last, wp.wave_id)
            if key not in from_cache:
                from_cache[key] = _pickups_from(day, last, wp, provider, cfg)
            for p in picks_by_wave.get(wp.wave_id, []) + from_cache[key]:
                k2 = (wp.wave_id, tuple(p.stops))
                if k2 in seen_keys or not day.ng_ok(p.stops):
                    continue
                seen_keys.add(k2)
                first = p.stops[0]
                fwd = provider.minutes(DEPOT, first)
                dead = provider.minutes(last, first)
                saving = back + fwd - dead
                if saving < cfg.chain_min_saving:
                    continue   # 施設に戻って出直すより大幅に遠回り
                tail = p.duration - fwd          # 迎えの1人目の家に着いてから施設に着くまで
                drive = d_body + dead + tail     # 待たずに回ったときの 出発→施設到着
                # 出発時刻 o の範囲: 送りの時間窓の中で、到着時刻に間に合い、空車待機が上限以内
                hi = min(d.start_hi, wp.time - drive)
                lo = max(d.start_lo, wp.time - cfg.early_eps - drive - cfg.chain_max_wait)
                if lo > hi:
                    continue
                cands.append((saving, wp, p, dead, tail, drive, lo, hi))
        # 便・人数ごとに最も良い組をまず選び、残りは短縮効果の大きい順
        cands.sort(key=lambda c: -c[0])
        chosen, groups = [], set()
        for c in cands:
            g = (c[1].wave_id, len(c[2].stops))
            if g not in groups and len(chosen) < cfg.chain_per_route:
                groups.add(g)
                chosen.append(c)
        for c in cands:
            if len(chosen) >= cfg.chain_per_route:
                break
            if c not in chosen:
                chosen.append(c)
        for saving, wp, p, dead, tail, drive, lo, hi in chosen:
            tail_offsets = [o - p.duration for o in p.offsets]   # 施設到着からの差（負）
            on_time_lo = max(lo, wp.time - drive - cfg.chain_max_wait)   # 定刻型の出発時刻の下限
            variants = []
            if on_time_lo <= hi:
                variants.append((None, on_time_lo))
            if cfg.early_eps > 0:
                variants.append((wp.time - cfg.early_eps, lo))
            for arrive_lo, vlo in variants:
                out.append(Route(
                    "", d.wave_id, "chain", d.stops + p.stops, list(d.offsets), wp.time - vlo,
                    max(d.seats, p.seats), max(d.walkers, p.walkers), max(d.max_need, p.max_need),
                    d.travel - provider.minutes(d.stops[-1], DEPOT) + dead + p.travel - provider.minutes(DEPOT, p.stops[0]),
                    max(d.wheelchairs, p.wheelchairs),
                    vlo, hi, DEPOT, DEPOT, None,
                    wave2=wp.wave_id, n_first=len(d.stops), fixed_end=wp.time,
                    idle_from=d_body + dead, tail_len=tail, tail_offsets=tail_offsets, deadhead=dead,
                    arrive_lo=arrive_lo, drop_lo=d.start_lo))
    return out


def _pickups_from(day: DayProblem, origin: str, w: Wave, provider: TravelTimeProvider,
                  cfg: RoutingConfig) -> list[Route]:
    """地点 origin（送りの最後の家）から出発して施設に戻る前提で、迎えの組み合わせを作る.

    便 w の利用者から chain_pick_max 人までの組をすべて試し、各組の最短の順番を求める。
    「origin から回って施設に着くまでの時間」が短い組を、人数ごとに chain_pick_keep 組返す。
    戻り値は施設発のルートとして計算し直したもの（連結は build_chains で行う）。
    """
    if not day.cars or not day.staff:
        return []
    cap = max(c.capacity for c in day.cars.values())
    max_walk = max(c.walker_slots for c in day.cars.values())
    max_chair = max(c.wheelchair_slots for c in day.cars.values())
    max_skill = max(s.care_skill for s in day.staff.values())
    uids = [u for u in w.user_ids
            if day.users[u].care_need <= max_skill and day.users[u].seats <= cap
            and (not day.users[u].walker or max_walk) and (not day.users[u].wheelchair or max_chair)]
    ids = [origin, DEPOT] + uids
    T = build_matrix(ids, provider)
    ix = {u: i for i, u in enumerate(ids)}
    svc = {u: day.users[u].service_min for u in uids}
    out: list[Route] = []
    for size in range(1, min(cfg.chain_pick_max, cap, len(uids)) + 1):
        best: list[tuple[int, tuple[str, ...]]] = []
        for combo in combinations(uids, size):
            us = [day.users[u] for u in combo]
            if (sum(u.seats for u in us) > cap or sum(u.walker for u in us) > max_walk
                    or sum(u.wheelchair for u in us) > max_chair or not day.ng_ok(list(combo))):
                continue
            cost, order = None, None
            for perm in permutations(combo):
                t, prev = 0, ix[origin]
                for u in perm:
                    t += T[prev][ix[u]] + svc[u]
                    prev = ix[u]
                t += T[prev][ix[DEPOT]]
                if cost is None or t < cost:
                    cost, order = t, perm
            best.append((cost, order))
        for cost, order in heapq.nsmallest(cfg.chain_pick_keep, best):
            r = _make_route(day, w, list(order), provider, cfg)
            out.append(r)
    return out
