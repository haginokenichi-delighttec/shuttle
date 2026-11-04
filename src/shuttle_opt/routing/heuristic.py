"""貪欲法による初期解.

CP-SAT（assign.py）は問題が大きくなると、時間内に良い解を見つけられないことがある。
歩行器・車椅子・介助2以上の利用者を含むルートを先に割り当て、歩行器・車椅子の枠のある車は
その枠が必要なルートのために温存する（2026-11-04）。
そこで、候補ルートから「多くの利用者をまとめて運べる・走行の短い」ものを順に選び、
空いている職員・車に割り当てていく簡単な方法で、ルールを満たす計画をまず1つ作る。
これを CP-SAT の初期解（ヒント）にして改善させる。CP-SAT が解を返せなかったときは、この計画をそのまま使う。

守るルールは CP-SAT と同じ（validate.py で確認できるもの）:
定員・歩行器・車椅子・介助・所有車・シフト（残業込み）・時間の重なり・直行直帰・昼休み・同乗NG（候補段階で除外済み）
"""
from __future__ import annotations

from collections import defaultdict

from .assign import compatible_cars, compatible_staff, start_options
from .config import RoutingConfig
from .data import DEPOT, DayProblem
from .route_gen import PlannedRoute, Pool
from . import lunch


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _direct_ok(day: DayProblem, sid: str, ps: list[PlannedRoute]) -> bool:
    st = day.staff[sid]
    if not ps:
        return True
    ps = sorted(ps, key=lambda p: p.start)
    if st.direct_start and ps[0].route.kind == "pickup" and ps[0].route.start_pt == DEPOT:
        return False
    if st.direct_end and ps[-1].route.kind == "dropoff" and ps[-1].route.end_pt == DEPOT:
        return False
    if any(p.route.start_pt != DEPOT for p in ps[1:]) or any(p.route.end_pt != DEPOT for p in ps[:-1]):
        return False
    return True


def greedy_plan(day: DayProblem, pool: Pool, cfg: RoutingConfig, use_help: bool = True) -> list[PlannedRoute]:
    pairs = set(day.pairs())
    p_max = max(s.priority for s in day.staff.values())
    opts = []
    for r in pool.routes:
        cars = [c for c in compatible_cars(day, r) if use_help or not day.cars[c].help]
        if not cars:
            continue
        n = len(r.stops)
        us = [day.users[u] for u in r.stops]
        # 条件の厳しい利用者（歩行器・車椅子・介助2以上）を含むルートを先に割り当てる
        hard = any(u.walker or u.wheelchair or u.care_need >= 2 for u in us)
        for o in start_options(r, cfg.start_step):
            for sid in compatible_staff(day, r, o):
                for cid in cars:
                    if (sid, cid) not in pairs:
                        continue
                    st, car = day.staff[sid], day.cars[cid]
                    ot = max(0, r.end_at(o) - st.end)
                    # 歩行器・車椅子の枠を使わないルートに、その枠のある車を使うのは後回し（枠を温存する）
                    waste = int(car.walker_slots > r.walkers) + int(car.wheelchair_slots > r.wheelchairs)
                    key = (not hard,                             # 条件の厳しい利用者を含むもの
                           -n,                                   # 多くの人を運べるもの
                           car.help, waste, ot,                  # ヘルプ車・枠の無駄・残業はなるべく避ける
                           r.travel / n,                         # 1人あたりの走行が短いもの
                           p_max - st.priority,                  # 優先度の高い職員
                           abs(o - r.start_hi) if r.kind == "pickup" else o - r.start_lo)
                    opts.append((key, r, o, sid, cid))
    opts.sort(key=lambda x: x[0])

    covered: set[tuple[str, str]] = set()
    staff_iv: dict[str, list[tuple[int, int]]] = defaultdict(list)
    car_iv: dict[str, list[tuple[int, int]]] = defaultdict(list)
    staff_ps: dict[str, list[PlannedRoute]] = defaultdict(list)
    plan: list[PlannedRoute] = []
    for _, r, o, sid, cid in opts:
        users = [(wid, u) for wid, uids in r.segments() for u in uids]
        if any(x in covered for x in users):
            continue
        iv = (o, r.end_at(o) + cfg.turnaround_min)
        if any(_overlaps(iv, x) for x in staff_iv[sid]) or any(_overlaps(iv, x) for x in car_iv[cid]):
            continue
        p = PlannedRoute(r, sid, cid, o)
        trial = staff_ps[sid] + [p]
        if not _direct_ok(day, sid, trial):
            continue
        st = day.staff[sid]
        if lunch.applies(st, cfg) and lunch.find_break(st, trial, cfg) is None:
            continue
        covered.update(users)
        staff_iv[sid].append(iv)
        car_iv[cid].append(iv)
        staff_ps[sid].append(p)
        plan.append(p)
    plan.sort(key=lambda p: (p.start, p.route.route_id))
    return plan


def dropped_of(day: DayProblem, plan: list[PlannedRoute]) -> dict[str, list[str]]:
    covered = {(wid, u) for p in plan for wid, uids in p.route.segments() for u in uids}
    out: dict[str, list[str]] = {}
    for w in day.waves:
        miss = [u for u in w.user_ids if (w.wave_id, u) not in covered]
        if miss:
            out[w.wave_id] = miss
    return out
