from pathlib import Path

from shuttle_opt.data_loader import Demand, Driver, ProblemData, load_problem
from shuttle_opt.model import SolveParams, build_and_solve

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"


def test_sample_feasible():
    data = load_problem(SAMPLE)
    res = build_and_solve(data, SolveParams(time_limit_sec=10))
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert not res.shortages
    # 勤務不可の枠に割り当てていない
    for (t, s), ds in res.assignment.items():
        for did in ds:
            assert (did, t, s) not in data.unavailable
    # 上限回数を守っている
    for d in data.drivers:
        assert res.shifts_per_driver[d.driver_id] <= d.max_shifts


def test_shortage_reported():
    data = ProblemData(
        drivers=[Driver("D01", 1, True)],
        demands=[Demand("2026-01-01", "AM", 2, 1)],
    )
    res = build_and_solve(data, SolveParams(time_limit_sec=5))
    assert res.shortages and res.shortages[0]["short"] == 1
