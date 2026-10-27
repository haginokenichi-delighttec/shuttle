"""Stage B: 候補ルートの選択と (職員, 車) の割当を同時に決める（CP-SAT, 集合分割）.

各候補ルートについて、施設出発時刻の候補を便の時間窓から数通り
（start_step 分刻み）作り、「ルート × 出発時刻」を1つの選択肢として扱う。
時刻が固定されるので、時間の重なりは「同じ時刻に動いている選択肢は1つまで」という
単純な足し算の制約で書け、CP-SAT が速く・安定して解ける。

変数
    w[r, o, s]  ルート r を出発時刻 o で職員 s が担当する
    v[r, o, c]  ルート r を出発時刻 o で車 c を使って走る
    z[r, o]     ルート r を出発時刻 o で採用する（= Σs w = Σc v）
ハード制約
    - 各便の各利用者は、採用ルートのうちちょうど1本に乗る（不可能な場合のみ大ペナルティで除外）
    - 同じ職員 / 同じ車は時間が重ならない（準備時間 turnaround を含む）
    - 職員のシフト内に収まる
    - 職員の介助スキル ≥ ルート内の最大介助レベル、車の定員・歩行器枠 ≥ ルートの人数・歩行器数
    - 職員所有車は持ち主のみ / 所有者は自車のみ（w[r,o,持ち主] == v[r,o,所有車]）
    - 同乗NGの利用者は同じルートに入れない（候補ルートの段階で除外）
    - 直行する職員: その日最初のルートが迎えなら、自宅発のルートでなければならない
      （自宅発ルートは必ずその日最初のルート）
    - 直帰する職員: その日最後のルートが送りなら、自宅着のルートでなければならない
      （自宅着ルートは必ずその日最後のルート）
    - 昼休み: 11:30〜13:30 の間に連続30分以上、送迎しない時間を必ず作る（lunch.py）。
      連結ルート（送り→迎え）の空車待機中に取ってもよい
    - 連結ルート: 送りで全員降ろしたら施設に戻らず次の迎えへ（候補は route_gen.build_chains）
目的（最小化）
    走行時間 + 1ルートごとの固定費
  + 優先度の低い職員を使うペナルティ + 担当ルート数の最大値
  + 迎えの早着・送りの出発遅れ（分）
  + 乗せられない利用者 × 大ペナルティ
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .config import RoutingConfig
from .data import DayProblem
from .route_gen import PlannedRoute, Pool, Route
from . import lunch


@dataclass
class AssignResult:
    status: str
    planned: list[PlannedRoute] = field(default_factory=list)
    dropped: dict[str, list[str]] = field(default_factory=dict)   # wave_id -> 乗せられなかった利用者
    objective: float | None = None
    bound: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")


def start_options(r: Route, step: int) -> list[int]:
    """出発時刻の候補. 迎えは遅い順（到着時刻ぎりぎり優先）、送りは早い順."""
    if r.kind == "pickup":
        opts = list(range(r.start_hi, r.start_lo - 1, -step))
        if opts[-1] != r.start_lo:
            opts.append(r.start_lo)
    else:
        opts = list(range(r.start_lo, r.start_hi + 1, step))
        if opts[-1] != r.start_hi:
            opts.append(r.start_hi)
    return opts


def compatible_staff(day: DayProblem, r: Route, start: int) -> list[str]:
    return [s.staff_id for s in day.staff.values()
            if (r.staff_only is None or r.staff_only == s.staff_id)
            and s.care_skill >= r.max_need and s.start <= start and r.end_at(start) <= s.end]


def compatible_cars(day: DayProblem, r: Route) -> list[str]:
    return [c.car_id for c in day.cars.values()
            if c.capacity >= r.seats and c.walker_slots >= r.walkers
            and c.wheelchair_slots >= r.wheelchairs]


def _add_no_overlap(m: cp_model.CpModel, items: list[tuple[int, int, cp_model.IntVar]]) -> None:
    """items: (開始, 終了(準備時間込み), 変数). 同時刻に動いているものは1つまで.

    区間が重なる組は、どれかの開始時刻で必ず同時に動いているので、
    各開始時刻について「その時刻を含む区間の合計 ≤ 1」とすれば十分。
    """
    starts = sorted({s for s, _, _ in items})
    for t in starts:
        active = [x for s, e, x in items if s <= t < e]
        if len(active) > 1:
            m.add(sum(active) <= 1)


def _add_direct_rules(m: cp_model.CpModel, day: DayProblem,
                      staff_opts: dict[str, list[tuple[Route, int, cp_model.IntVar]]]) -> None:
    """直行・直帰の職員について「最初／最後のルート」の決まりを入れる.

    first[x] = 選択肢 x がその職員のその日最初のルート（last も同様）。
    最初のルートの出発時刻 E は、担当する全ルートの出発時刻以下になるようにする。
    """
    BIG = 24 * 60 * 2
    for sid, opts in staff_opts.items():
        st = day.staff[sid]
        if not (st.direct_start or st.direct_end) or not opts:
            continue
        xs = [x for _, _, x in opts]
        has_any = m.new_bool_var(f"has_any[{sid}]")
        m.add_max_equality(has_any, xs)
        if st.direct_start:
            f = [m.new_bool_var(f"first[{sid},{i}]") for i in range(len(opts))]
            m.add(sum(f) == has_any)
            E = sum(o * fi for (_, o, _), fi in zip(opts, f))
            for (r, o, x), fi in zip(opts, f):
                m.add(fi <= x)
                m.add(E <= o + BIG * (1 - x))
                if r.kind == "pickup" and r.start_pt == "DEPOT":
                    m.add(fi == 0)        # 施設発の迎えは、その日最初のルートにできない
                if r.start_pt != "DEPOT":
                    m.add(x <= fi)        # 自宅発ルートは必ず最初
        if st.direct_end:
            l = [m.new_bool_var(f"last[{sid},{i}]") for i in range(len(opts))]
            m.add(sum(l) == has_any)
            L = sum(r.end_at(o) * li for (r, o, _), li in zip(opts, l))
            for (r, o, x), li in zip(opts, l):
                m.add(li <= x)
                m.add(r.end_at(o) <= L + BIG * (1 - x))
                if r.kind == "dropoff" and r.end_pt == "DEPOT":
                    m.add(li == 0)        # 施設着の送りは、その日最後のルートにできない
                if r.end_pt != "DEPOT":
                    m.add(x <= li)        # 自宅着ルートは必ず最後


def select_and_assign(day: DayProblem, pool: Pool, cfg: RoutingConfig,
                      hint: list[PlannedRoute] | None = None, time_limit: float | None = None) -> AssignResult:
    """hint: 先に解いた計画（あれば初期解として渡し、そこから改善させる）."""
    m = cp_model.CpModel()
    owner_of = {c.car_id: c.owner for c in day.cars.values() if c.owner}

    z: dict[tuple[str, int], cp_model.IntVar] = {}
    w: dict[tuple[str, int, str], cp_model.IntVar] = {}
    v: dict[tuple[str, int, str], cp_model.IntVar] = {}
    by_staff, by_car = defaultdict(list), defaultdict(list)
    staff_opts = defaultdict(list)   # sid -> [(route, 出発時刻, 変数)]
    cover = defaultdict(list)
    routes = {r.route_id: r for r in pool.routes}
    cost_terms = []

    for r in pool.routes:
        rid = r.route_id
        cars_ok = compatible_cars(day, r)
        if not cars_ok:
            continue
        for o in start_options(r, cfg.start_step):
            staff_ok = compatible_staff(day, r, o)
            # 所有車ルールで成り立つ組み合わせがあるか
            pairs_ok = [(s, c) for s, c in day.pairs() if s in staff_ok and c in cars_ok]
            if not pairs_ok:
                continue
            staff_ok = sorted({s for s, _ in pairs_ok})
            cars_used = sorted({c for _, c in pairs_ok})
            zz = m.new_bool_var(f"z[{rid},{o}]")
            z[rid, o] = zz
            end = r.end_at(o) + cfg.turnaround_min
            for sid in staff_ok:
                x = m.new_bool_var(f"w[{rid},{o},{sid}]")
                w[rid, o, sid] = x
                by_staff[sid].append((o, end, x))
                staff_opts[sid].append((r, o, x))
            for cid in cars_used:
                y = m.new_bool_var(f"v[{rid},{o},{cid}]")
                v[rid, o, cid] = y
                by_car[cid].append((o, end, y))
            m.add(sum(w[rid, o, s] for s in staff_ok) == zz)
            m.add(sum(v[rid, o, c] for c in cars_used) == zz)
            for cid, owner in owner_of.items():
                xo, yo = w.get((rid, o, owner)), v.get((rid, o, cid))
                if xo is not None and yo is not None:
                    m.add(xo == yo)
                elif xo is not None:
                    m.add(xo == 0)
                elif yo is not None:
                    m.add(yo == 0)
            for wid, uids in r.segments():
                for uid in uids:
                    cover[wid, uid].append(zz)
            shift = (r.start_hi - o) if r.kind == "pickup" else (o - r.start_lo)
            cost_terms.append((r.travel + cfg.vehicle_fixed_cost + cfg.w_time_shift * shift) * zz)

    missed = {}
    for wv in day.waves:
        for uid in wv.user_ids:
            miss = m.new_bool_var(f"miss[{wv.wave_id},{uid}]")
            missed[wv.wave_id, uid] = miss
            m.add(sum(cover.get((wv.wave_id, uid), [])) + miss == 1)

    # 昼休み: 対象の職員ごとに 30分の休憩枠を1つ選ぶ。休憩枠は、その職員が担当するルートの
    # 「手の離せない時間」（出発〜帰着後の準備。連結ルートの空車待機は除く）と重ならないこと。
    # → 連結ルートの途中、利用者を乗せていない待機時間に昼休みを取ってもよい。
    for sid, st in day.staff.items():
        if not lunch.applies(st, cfg):
            continue
        ws, we = lunch.window(cfg)
        items, brs = [], []
        for b in lunch.break_starts(cfg):
            br = m.new_bool_var(f"lunch[{sid},{b}]")
            brs.append(br)
            items.append((b, b + cfg.lunch_min, br))
        for r, o, x in staff_opts.get(sid, []):
            for ps, pe in r.busy_parts(o, cfg.turnaround_min):
                if ps < we and ws < pe:
                    items.append((ps, pe, x))
        m.add_exactly_one(brs)
        _add_no_overlap(m, items)   # 休憩枠と「手の離せない時間」が同時刻に重ならない

    for items in by_staff.values():
        _add_no_overlap(m, items)
    for items in by_car.values():
        _add_no_overlap(m, items)
    _add_direct_rules(m, day, staff_opts)

    p_max = max(s.priority for s in day.staff.values())
    max_load = m.new_int_var(0, 50, "max_load")
    for sid, items in by_staff.items():
        m.add(max_load >= sum(x for _, _, x in items))
    m.minimize(
        sum(cost_terms)
        + cfg.w_priority * sum((p_max - day.staff[s].priority) * x for (rid, o, s), x in w.items())
        + cfg.w_max_load * max_load
        + cfg.drop_penalty * sum(missed.values())
    )

    if hint is not None:
        chosen = {(p.route.route_id, p.start): p for p in hint}
        for (rid, o), zz in z.items():
            m.add_hint(zz, (rid, o) in chosen)
        for (rid, o, sid), x in w.items():
            p = chosen.get((rid, o))
            m.add_hint(x, p is not None and p.staff_id == sid)
        for (rid, o, cid), y in v.items():
            p = chosen.get((rid, o))
            m.add_hint(y, p is not None and p.car_id == cid)
        covered = {(wid, u) for p in hint for wid, uids in p.route.segments() for u in uids}
        for key, miss in missed.items():
            m.add_hint(miss, key not in covered)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit or cfg.assign_time_limit_sec
    solver.parameters.num_workers = cfg.num_workers
    st = solver.solve(m)
    res = AssignResult(solver.status_name(st))
    if not res.ok:
        return res
    res.objective = solver.objective_value
    res.bound = solver.best_objective_bound
    staff_of = {(rid, o): s for (rid, o, s), x in w.items() if solver.value(x)}
    car_of = {(rid, o): c for (rid, o, c), y in v.items() if solver.value(y)}
    for (rid, o), zz in z.items():
        if solver.value(zz):
            res.planned.append(PlannedRoute(routes[rid], staff_of[rid, o], car_of[rid, o], o))
    res.planned.sort(key=lambda p: (p.start, p.route.route_id))
    for (wid, uid), miss in missed.items():
        if solver.value(miss):
            res.dropped.setdefault(wid, []).append(uid)
    return res
