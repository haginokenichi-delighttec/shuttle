"""パイプライン: 候補ルート生成（Stage A）→ 選択と割当（Stage B）."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .assign import compatible_cars, compatible_staff, select_and_assign
from .config import RoutingConfig
from .data import DayProblem, min_to_hm
from .route_gen import PlannedRoute, generate_pool
from .travel_time import TravelTimeProvider


@dataclass
class DayPlan:
    ok: bool
    status: str
    planned: list[PlannedRoute] = field(default_factory=list)
    dropped: dict[str, list[str]] = field(default_factory=dict)   # wave_id -> 乗せられなかった利用者
    log: list[str] = field(default_factory=list)

    @property
    def total_travel(self) -> int:
        return sum(p.route.travel for p in self.planned)


def plan_day(day: DayProblem, provider: TravelTimeProvider, cfg: RoutingConfig | None = None) -> DayPlan:
    cfg = cfg or RoutingConfig()
    log = []
    t0 = time.perf_counter()
    pool = generate_pool(day, provider, cfg)
    t1 = time.perf_counter()
    log.append(f"Stage A: 候補ルート {len(pool.routes)} 本（{t1 - t0:.1f} 秒）")
    for wid, us in pool.uncoverable.items():
        log.append(f"  [警告] {wid}: 条件に合う職員・車がいない利用者 {', '.join(us)}")
    res = select_and_assign(day, pool, cfg)
    t2 = time.perf_counter()
    gap = ""
    if res.ok and res.status != "OPTIMAL" and res.bound is not None:
        gap = f" / 下界 {res.bound:.0f} に対し目的値 {res.objective:.0f}"
    log.append(f"Stage B: {res.status} / 採用 {len(res.planned)} 本（{t2 - t1:.1f} 秒）{gap}")
    if res.ok:
        for wid, us in res.dropped.items():
            for u in us:
                log.append(f"  [警告] {wid}: {u} を乗せられません（{_reason(day, pool, wid, u)}）")
    return DayPlan(res.ok, res.status, res.planned, res.dropped, log)


def _reason(day: DayProblem, pool, wave_id: str, uid: str) -> str:
    """乗せられなかった理由の目安."""
    u = day.users[uid]
    if uid in pool.uncoverable.get(wave_id, []):
        if not any(s.care_skill >= u.care_need for s in day.staff.values()):
            return f"介助レベル{u.care_need}に対応できる職員が当日いない"
        if u.walker and not any(c.walker_slots for c in day.cars.values()):
            return "歩行器を積める車が当日ない"
        if u.wheelchair and not any(c.wheelchair_slots for c in day.cars.values()):
            return "車椅子を載せられる車が当日ない"
        return "その時間帯に出勤している職員がいない"
    cands = [r for r in pool.routes if r.wave_id == wave_id and uid in r.stops]
    pairs = set(day.pairs())

    def ok(r) -> bool:
        cars = set(compatible_cars(day, r))
        for o in range(r.start_lo, r.start_hi + 1):
            if any((s, c) in pairs for s in compatible_staff(day, r, o) for c in cars):
                return True
        return False

    if not any(ok(r) for r in cands):
        return (f"介助レベル{u.care_need}・歩行器{u.walker}・車椅子{u.wheelchair}の条件を満たす"
                "職員・車の組がその時間帯にいない")
    return "職員・車の空き時間が足りない（同じ時間帯の便と重なる）"


def describe(day: DayProblem, plan: DayPlan) -> str:
    """職員ごとのタイムライン（テキスト）."""
    lines = []
    for sid in sorted(day.staff):
        s = day.staff[sid]
        ps = [p for p in plan.planned if p.staff_id == sid]
        lines.append(f"■ {sid}（優先度{s.priority}・介助{s.care_skill}・勤務 {min_to_hm(s.start)}-{min_to_hm(s.end)}）"
                     f" 担当 {len(ps)} 本")
        for p in ps:
            stops = " → ".join(f"{u}({min_to_hm(t)})" for u, t in zip(p.route.stops, p.eta))
            lines.append(f"   {min_to_hm(p.start)}-{min_to_hm(p.end)} {p.car_id} {p.route.wave_id}: "
                         f"施設 → {stops} → 施設")
    return "\n".join(lines)
