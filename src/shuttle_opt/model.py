"""送迎シフト割当モデル (CP-SAT).

決定変数
    x[d, t, s] ∈ {0,1} : ドライバー d が 日付 t・便 s (AM/PM) を担当するか

制約 (ハード)
    1. 勤務不可 (unavailability.csv) の枠には割り当てない
    2. 各ドライバーの期間内の担当回数 ≤ max_shifts
    3. 連続勤務日数 ≤ max_consecutive_days

制約 (ソフト: 違反にペナルティ)
    4. 各枠の必要人数 (required) を満たす      … 不足 short[t, s]
    5. 各枠の車椅子対応人数を満たす           … 不足 short_wc[t, s]

目的関数 (最小化)
    W_SHORT * 不足人数合計 + W_FAIR * (担当回数の最大 - 最小)

※ 必要人数を満たせない場合も解を返し、どの枠が不足しているかを出力します。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .data_loader import SLOTS, ProblemData


@dataclass
class SolveParams:
    time_limit_sec: float = 30.0
    max_consecutive_days: int = 5
    w_short: int = 1000
    w_fair: int = 10
    num_workers: int = 8


@dataclass
class SolveResult:
    status: str
    objective: float | None
    assignment: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (date, slot) -> drivers
    shifts_per_driver: dict[str, int] = field(default_factory=dict)
    shortages: list[dict] = field(default_factory=list)


def build_and_solve(data: ProblemData, params: SolveParams | None = None) -> SolveResult:
    params = params or SolveParams()
    m = cp_model.CpModel()

    drivers = data.drivers
    dates = data.dates
    demand = {(dm.date, dm.slot): dm for dm in data.demands}
    keys = list(demand.keys())

    # --- 変数 ---
    x = {
        (d.driver_id, t, s): m.new_bool_var(f"x_{d.driver_id}_{t}_{s}")
        for d in drivers
        for (t, s) in keys
    }
    short = {k: m.new_int_var(0, demand[k].required, f"short_{k}") for k in keys}
    short_wc = {k: m.new_int_var(0, demand[k].required_wheelchair, f"shortwc_{k}") for k in keys}

    # --- 1. 勤務不可 ---
    for (did, t, s) in data.unavailable:
        if (did, t, s) in x:
            m.add(x[did, t, s] == 0)

    # --- 4,5. 必要人数 (過剰配置はしない) ---
    for k in keys:
        t, s = k
        assigned = sum(x[d.driver_id, t, s] for d in drivers)
        m.add(assigned + short[k] == demand[k].required)
        wc = sum(x[d.driver_id, t, s] for d in drivers if d.wheelchair_ok)
        m.add(wc + short_wc[k] >= demand[k].required_wheelchair)

    # --- 2. 担当回数上限 ---
    n_shifts = {}
    for d in drivers:
        total = sum(x[d.driver_id, t, s] for (t, s) in keys)
        m.add(total <= d.max_shifts)
        n_shifts[d.driver_id] = total

    # --- 3. 連続勤務日数 ---
    L = params.max_consecutive_days
    for d in drivers:
        work = {}
        for t in dates:
            w = m.new_bool_var(f"work_{d.driver_id}_{t}")
            xs = [x[d.driver_id, t, s] for s in SLOTS if (d.driver_id, t, s) in x]
            if xs:
                m.add_max_equality(w, xs)
            else:
                m.add(w == 0)
            work[t] = w
        for i in range(len(dates) - L):
            m.add(sum(work[dates[j]] for j in range(i, i + L + 1)) <= L)

    # --- 目的関数: 不足最小化 + 公平性 ---
    max_n = m.new_int_var(0, len(keys), "max_n")
    min_n = m.new_int_var(0, len(keys), "min_n")
    m.add_max_equality(max_n, list(n_shifts.values()))
    m.add_min_equality(min_n, list(n_shifts.values()))
    m.minimize(
        params.w_short * (sum(short.values()) + sum(short_wc.values()))
        + params.w_fair * (max_n - min_n)
    )

    # --- 求解 ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = params.time_limit_sec
    solver.parameters.num_workers = params.num_workers
    status = solver.solve(m)
    status_name = solver.status_name(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResult(status=status_name, objective=None)

    res = SolveResult(status=status_name, objective=solver.objective_value)
    for (t, s) in keys:
        res.assignment[(t, s)] = [d.driver_id for d in drivers if solver.value(x[d.driver_id, t, s])]
        if solver.value(short[t, s]) or solver.value(short_wc[t, s]):
            res.shortages.append(
                {"date": t, "slot": s,
                 "short": solver.value(short[t, s]),
                 "short_wheelchair": solver.value(short_wc[t, s])}
            )
    res.shifts_per_driver = {did: int(solver.value(v)) for did, v in n_shifts.items()}
    return res
