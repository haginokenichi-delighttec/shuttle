"""パイプライン: 候補ルート生成（Stage A）→ 選択と割当（Stage B）."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .assign import compatible_cars, compatible_staff, select_and_assign
from .config import RoutingConfig
from .data import DayProblem, min_to_hm
from .route_gen import PlannedRoute, generate_pool
from . import lunch
from .travel_time import TravelTimeProvider


@dataclass
class DayPlan:
    ok: bool
    status: str
    planned: list[PlannedRoute] = field(default_factory=list)
    dropped: dict[str, list[str]] = field(default_factory=dict)   # wave_id -> 乗せられなかった利用者
    log: list[str] = field(default_factory=list)
    breaks: dict[str, tuple[int, int] | None] = field(default_factory=dict)   # 職員 -> 昼休み（開始, 終了）

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
    n_chain = sum(r.is_chain for r in pool.routes)
    if n_chain:
        log.append(f"  連結ルート（送り→施設に戻らず迎え）: 候補 {n_chain} 本 / 採用 "
                   f"{sum(p.route.is_chain for p in res.planned)} 本")
    t2 = time.perf_counter()
    gap = ""
    if res.ok and res.status != "OPTIMAL" and res.bound is not None:
        gap = f" / 下界 {res.bound:.0f} に対し目的値 {res.objective:.0f}"
    log.append(f"Stage B: {res.status} / 採用 {len(res.planned)} 本（{t2 - t1:.1f} 秒）{gap}")
    if res.ok:
        for wid, us in res.dropped.items():
            for u in us:
                log.append(f"  [警告] {wid}: {u} を乗せられません（{_reason(day, pool, wid, u)}）")
    plan = DayPlan(res.ok, res.status, res.planned, res.dropped, log)
    if res.ok:
        plan.breaks = lunch.breaks(day, res.planned, cfg)
    return plan


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
        direct = "・直行" * s.direct_start + "・直帰" * s.direct_end
        br = plan.breaks.get(sid)
        lunch_txt = f" ／ 昼休み {min_to_hm(br[0])}-{min_to_hm(br[1])}" if br else ""
        lines.append(f"■ {sid}（優先度{s.priority}・介助{s.care_skill}{direct}・勤務 {min_to_hm(s.start)}-{min_to_hm(s.end)}）"
                     f" 担当 {len(ps)} 本{lunch_txt}")
        for p in ps:
            r = p.route
            parts = [f"{u}({min_to_hm(t)})" for u, t in zip(r.stops, p.eta)]
            sp = "施設" if r.start_pt == "DEPOT" else "自宅"
            ep = "施設" if r.end_pt == "DEPOT" else "自宅"
            if r.is_chain:
                a, b = p.idle
                wait = f" ⇒ [空車 {min_to_hm(a)}-{min_to_hm(b)} 待機{b - a}分] ⇒ "
                route_txt = " → ".join(parts[:r.n_first]) + wait + " → ".join(parts[r.n_first:])
                label = f"{r.wave_id}→{r.wave2}"
            else:
                route_txt, label = " → ".join(parts), r.wave_id
            lines.append(f"   {min_to_hm(p.start)}-{min_to_hm(p.end)} {p.car_id} {label}: "
                         f"{sp} → {route_txt} → {ep}")
    return "\n".join(lines)
