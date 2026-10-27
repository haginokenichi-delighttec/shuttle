"""送迎ルート最適化のテスト（小さな架空データで確認）."""
from pathlib import Path

import pandas as pd
import pytest

from shuttle_opt.routing.config import RoutingConfig
from shuttle_opt.routing.data import hm_to_min, load_day, min_to_hm
from shuttle_opt.routing.pipeline import plan_day
from shuttle_opt.routing.travel_time import DEPOT, HaversineProvider, haversine_km
from shuttle_opt.routing.validate import validate

DATE = "2026-12-01"


def _write(d: Path, users, staff, shifts, cars, plans, att):
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"lat": 35.0, "lng": 135.0}]).to_csv(d / "facility.csv", index=False)
    pd.DataFrame(users, columns=["user_id", "lat", "lng", "care_need", "walker", "seats", "service_min"]).to_csv(
        d / "users.csv", index=False)
    pd.DataFrame(staff, columns=["staff_id", "priority", "care_skill"]).to_csv(d / "staff.csv", index=False)
    pd.DataFrame(shifts, columns=["date", "staff_id", "start", "end"]).to_csv(d / "shifts.csv", index=False)
    pd.DataFrame(cars, columns=["car_id", "capacity", "walker_slots", "owner_staff_id"]).to_csv(
        d / "cars.csv", index=False)
    pd.DataFrame(plans, columns=["plan_id", "arrive", "depart"]).to_csv(d / "plans.csv", index=False)
    pd.DataFrame(att, columns=["date", "user_id", "plan_id"]).to_csv(d / "attendance.csv", index=False)


@pytest.fixture
def small(tmp_path):
    users = [(f"U{i}", 35.0 + 0.004 * i, 135.0 + 0.003 * (i % 3), 1, 0, 1, 3) for i in range(1, 7)]
    users[0] = ("U1", 35.004, 135.003, 3, 1, 1, 5)          # 介助レベル3・歩行器あり
    staff = [("S1", 5, 3), ("S2", 3, 2), ("S3", 1, 1), ("S4", 4, 2)]
    shifts = [(DATE, s, "07:30", "17:00") for s, *_ in staff]
    cars = [("C1", 4, 1, ""), ("C2", 4, 0, ""), ("C3", 3, 0, "S4")]
    plans = [("P1", "09:00", "12:00"), ("P2", "10:00", "15:00")]
    att = [(DATE, f"U{i}", "P1" if i <= 4 else "P2") for i in range(1, 7)]
    _write(tmp_path, users, staff, shifts, cars, plans, att)
    return tmp_path


def _provider(day, cfg):
    return HaversineProvider(day.points(), cfg.detour, cfg.speed_kmh)


def test_time_conversion():
    assert hm_to_min("09:30") == 570
    assert min_to_hm(570) == "09:30"


def test_haversine_one_degree_latitude():
    assert haversine_km(35.0, 135.0, 36.0, 135.0) == pytest.approx(111.2, abs=0.2)


def test_pairs_owner_rule(small):
    day = load_day(small, DATE)
    pairs = set(day.pairs())
    assert ("S4", "C3") in pairs                 # 所有者は自分の車
    assert ("S4", "C1") not in pairs             # 所有者は他の車を使わない
    assert ("S1", "C3") not in pairs             # 他人の所有車は使わない


def test_wheelchair_needs_wheelchair_car(tmp_path):
    users = [("U1", 35.004, 135.003, 1, 0, 1, 3), ("U2", 35.006, 135.0, 1, 0, 1, 3)]
    _write(tmp_path, users, [("S1", 5, 3)], [(DATE, "S1", "07:30", "17:00")],
           [("C1", 4, 1, ""), ("C2", 4, 0, "")], [("P1", "09:00", "12:00")],
           [(DATE, "U1", "P1"), (DATE, "U2", "P1")])
    df = pd.read_csv(tmp_path / "users.csv")
    df["wheelchair"] = [1, 0]
    df.to_csv(tmp_path / "users.csv", index=False)
    df = pd.read_csv(tmp_path / "cars.csv")
    df["wheelchair_slots"] = [0, 1]
    df.to_csv(tmp_path / "cars.csv", index=False)
    cfg = RoutingConfig(assign_time_limit_sec=5)
    day = load_day(tmp_path, DATE)
    prov = _provider(day, cfg)
    plan = plan_day(day, prov, cfg)
    assert plan.dropped == {}
    for p in plan.planned:
        if "U1" in p.route.stops:
            assert p.car_id == "C2"
    assert validate(day, plan.planned, prov, cfg) == []


def test_date_and_blank_normalization(tmp_path):
    users = [("U1", 35.004, 135.003, "", "", "", "")]
    _write(tmp_path, users, [("S1", 5, 3)], [("2026/12/1", "S1", "7:30", "17:00")],
           [("C1", 4, 0, "")], [("P1", "9:00", "12:00")], [("2026/12/01", "U1", "P1")])
    day = load_day(tmp_path, DATE)
    u = day.users["U1"]
    assert (u.care_need, u.walker, u.wheelchair, u.seats, u.service_min) == (1, 0, 0, 1, 5)
    assert "S1" in day.staff


def test_waves(small):
    day = load_day(small, DATE)
    assert [w.wave_id for w in day.waves] == ["迎え09:00_P1", "迎え10:00_P2", "送り12:00_P1", "送り15:00_P2"]


def test_small_plan_covers_everyone(small):
    cfg = RoutingConfig(assign_time_limit_sec=10)
    day = load_day(small, DATE)
    prov = _provider(day, cfg)
    plan = plan_day(day, prov, cfg)
    assert plan.ok
    assert plan.dropped == {}
    assert validate(day, plan.planned, prov, cfg) == []
    # 介助レベル3の U1 は介助スキル3の S1 が担当する
    for p in plan.planned:
        if "U1" in p.route.stops:
            assert p.staff_id == "S1"
            assert day.cars[p.car_id].walker_slots >= 1


def test_low_priority_staff_avoided(small):
    cfg = RoutingConfig(assign_time_limit_sec=10)
    day = load_day(small, DATE)
    plan = plan_day(day, _provider(day, cfg), cfg)
    assert all(p.staff_id != "S3" for p in plan.planned)   # 優先度1の職員は使わずに済む


def test_skill_shortage_is_reported(tmp_path):
    users = [("U1", 35.004, 135.003, 3, 0, 1, 3), ("U2", 35.006, 135.0, 1, 0, 1, 3)]
    staff = [("S1", 5, 2)]                                   # 介助3に対応できる職員がいない
    _write(tmp_path, users, staff, [(DATE, "S1", "07:30", "17:00")], [("C1", 4, 0, "")],
           [("P1", "09:00", "12:00")], [(DATE, "U1", "P1"), (DATE, "U2", "P1")])
    cfg = RoutingConfig(assign_time_limit_sec=5)
    day = load_day(tmp_path, DATE)
    prov = _provider(day, cfg)
    plan = plan_day(day, prov, cfg)
    assert plan.ok
    assert plan.dropped == {"迎え09:00_P1": ["U1"], "送り12:00_P1": ["U1"]}
    assert validate(day, plan.planned, prov, cfg, allow_dropped=plan.dropped) == []


def test_dummy_day_is_valid(tmp_path):
    """ダミーデータ生成スクリプトで作った1日分で、検証が通ること."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from make_dummy_routing import make
    make(tmp_path, seed=1)
    cfg = RoutingConfig(assign_time_limit_sec=15)
    day = load_day(tmp_path, "2026-11-04")
    prov = _provider(day, cfg)
    plan = plan_day(day, prov, cfg)
    assert plan.ok
    assert validate(day, plan.planned, prov, cfg, allow_dropped=plan.dropped) == []


def test_google_maps_url():
    from shuttle_opt.routing.maps import route_url
    url = route_url([(36.1, 137.9), (36.2, 137.95)], (36.25, 137.98))
    assert url == ("https://www.google.com/maps/dir/?api=1"
                   "&waypoints=36.100000,137.900000%7C36.200000,137.950000"
                   "&destination=36.250000,137.980000&travelmode=driving&dir_action=navigate")
    assert "origin=" not in url


def test_google_maps_preview_url():
    from shuttle_opt.routing.maps import route_url
    url = route_url([(36.1, 137.9)], (36.25, 137.98), preview=True)
    assert url == ("https://www.google.com/maps/dir/?api=1&origin=36.250000,137.980000"
                   "&waypoints=36.100000,137.900000"
                   "&destination=36.250000,137.980000&travelmode=driving")


def _load_fetch_tool():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "tools" / "fetch_google_matrix.py"
    spec = importlib.util.spec_from_file_location("fetch_google_matrix", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fetch_matrix_plan_blocks():
    fm = _load_fetch_tool()
    ids = [f"U{i:02d}" for i in range(30)]
    blocks = fm.plan_blocks(ids, set(ids), set(), block=25)
    assert len(blocks) == 4
    assert all(len(o) * len(d) <= 625 for o, d in blocks)
    # 全部取得済みで座標変更なし → 取得なし
    done = {(a, b) for a in ids for b in ids if a != b}
    assert fm.plan_blocks(ids, set(), done) == []
    # 1人だけ新規 → その人の行と列だけ
    blocks = fm.plan_blocks(ids + ["NEW"], {"NEW"}, done)
    n = sum(len(o) * len(d) for o, d in blocks)
    assert n < 2 * 31 + 31


def test_fetch_matrix_end_to_end_with_fake_api(tmp_path, monkeypatch):
    fm = _load_fetch_tool()
    pd.DataFrame([{"lat": 35.0, "lng": 135.0}]).to_csv(tmp_path / "facility.csv", index=False)
    pd.DataFrame([{"user_id": f"U{i}", "lat": 35.0 + 0.01 * i, "lng": 135.0} for i in range(1, 4)]).to_csv(
        tmp_path / "users.csv", index=False)
    calls = []

    def fake_post(key, origins, dests, pref):
        calls.append((len(origins), len(dests)))
        return [{"originIndex": i, "destinationIndex": j, "duration": f"{60 * (i + j) + 30}s",
                 "distanceMeters": 1000, "condition": "ROUTE_EXISTS", "status": {}}
                for i in range(len(origins)) for j in range(len(dests))]

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "dummy")
    assert fm.main(["--data", str(tmp_path)], post=fake_post) == 0
    df = pd.read_csv(tmp_path / "travel_matrix.csv")
    assert len(df) == 4 * 3                    # 4地点の自分以外の組
    assert (df["minutes"] >= 1).all()
    # 2回目は取得なし
    calls.clear()
    assert fm.main(["--data", str(tmp_path)], post=fake_post) == 0
    assert calls == []
    # 計画側で読み込めること
    from shuttle_opt.routing.travel_time import CachedMatrixProvider
    prov = CachedMatrixProvider(tmp_path / "travel_matrix.csv")
    assert prov.minutes("DEPOT", "U1") >= 1


def test_ng_pair_never_share_a_car(tmp_path):
    users = [(f"U{i}", 35.0 + 0.002 * i, 135.0, 1, 0, 1, 3) for i in range(1, 5)]
    _write(tmp_path, users, [("S1", 5, 3), ("S2", 4, 3)],
           [(DATE, "S1", "07:30", "17:00"), (DATE, "S2", "07:30", "17:00")],
           [("C1", 4, 0, ""), ("C2", 4, 0, "")], [("P1", "09:00", "12:00")],
           [(DATE, f"U{i}", "P1") for i in range(1, 5)])
    pd.DataFrame([{"user_a": "U1", "user_b": "U2"}]).to_csv(tmp_path / "ng_pairs.csv", index=False)
    cfg = RoutingConfig(assign_time_limit_sec=10)
    day = load_day(tmp_path, DATE)
    prov = _provider(day, cfg)
    plan = plan_day(day, prov, cfg)
    assert plan.dropped == {}
    for p in plan.planned:
        assert not {"U1", "U2"} <= set(p.route.stops)
    assert validate(day, plan.planned, prov, cfg) == []


def test_direct_commute_starts_and_ends_at_home(tmp_path):
    users = [(f"U{i}", 35.0 + 0.003 * i, 135.0, 1, 0, 1, 3) for i in range(1, 4)]
    _write(tmp_path, users, [("S1", 5, 3)], [(DATE, "S1", "07:30", "17:00")],
           [("C1", 4, 0, "S1")], [("P1", "09:00", "12:00")],
           [(DATE, f"U{i}", "P1") for i in range(1, 4)])
    df = pd.read_csv(tmp_path / "staff.csv")
    df["home_lat"], df["home_lng"], df["direct_start"], df["direct_end"] = 35.02, 135.01, 1, 1
    df.to_csv(tmp_path / "staff.csv", index=False)
    cfg = RoutingConfig(assign_time_limit_sec=10)
    day = load_day(tmp_path, DATE)
    prov = _provider(day, cfg)
    plan = plan_day(day, prov, cfg)
    assert plan.dropped == {}
    ps = sorted(plan.planned, key=lambda p: p.start)
    assert ps[0].route.kind == "pickup" and ps[0].route.start_pt == "HOME_S1"
    assert ps[-1].route.kind == "dropoff" and ps[-1].route.end_pt == "HOME_S1"
    assert validate(day, plan.planned, prov, cfg) == []
    from shuttle_opt.routing.maps import route_url
    url = route_url([(35.0, 135.0)], (35.02, 135.01), preview=True, origin=(35.1, 135.1))
    assert "origin=35.100000,135.100000" in url and "destination=35.020000,135.010000" in url


def test_lunch_break_is_kept(tmp_path):
    """11:30〜13:30 に連続30分の休憩が取れること（1人で昼をまたぐ便を回す日）."""
    users = [(f"U{i}", 35.0 + 0.01 * i, 135.0, 1, 0, 1, 5) for i in range(1, 4)]
    _write(tmp_path, users, [("S1", 5, 3)], [(DATE, "S1", "08:00", "17:00")],
           [("C1", 4, 0, "")], [("P1", "09:00", "12:00"), ("P2", "13:30", "16:00")],
           [(DATE, "U1", "P1"), (DATE, "U2", "P1"), (DATE, "U3", "P2")])
    cfg = RoutingConfig(assign_time_limit_sec=10)
    day = load_day(tmp_path, DATE)
    prov = _provider(day, cfg)
    plan = plan_day(day, prov, cfg)
    assert plan.ok and plan.dropped == {}
    b = plan.breaks["S1"]
    assert b is not None and 30 <= b[1] - b[0] <= 60
    assert hm_to_min("11:30") <= b[0] and b[1] <= hm_to_min("13:30")
    assert validate(day, plan.planned, prov, cfg) == []


def test_lunch_find_break():
    from shuttle_opt.routing import lunch
    from shuttle_opt.routing.data import Staff
    from shuttle_opt.routing.route_gen import PlannedRoute, Route
    cfg = RoutingConfig()
    st = Staff("S1", 3, 3, hm_to_min("08:00"), hm_to_min("17:00"))
    r = Route("r", "w", "dropoff", ["U1"], [5], 40, 1, 0, 1, 30)
    # 12:00-12:40 送り（準備10分で 12:50 まで）→ 11:30-12:00 か 12:50-13:30 の長い方（40分）
    planned = [PlannedRoute(r, "S1", "C1", hm_to_min("12:00"))]
    assert lunch.find_break(st, planned, cfg) == (hm_to_min("12:50"), hm_to_min("13:30"))
    # 何も担当しなければ 11:30 から60分
    assert lunch.find_break(st, [], cfg) == (hm_to_min("11:30"), hm_to_min("12:30"))
    # 11:40-13:10 の長いルート → 休憩が取れない
    r2 = Route("r2", "w", "dropoff", ["U1"], [5], 90, 1, 0, 1, 30)
    assert lunch.find_break(st, [PlannedRoute(r2, "S1", "C1", hm_to_min("11:40"))], cfg) is None


def test_chain_dropoff_then_pickup(tmp_path):
    """送りの最後の家の近くに次の迎えがあるとき、施設に戻らず連結ルートを使う."""
    # 施設(35.0,135.0)から約5km北に利用者が集まっている
    users = [("U1", 35.045, 135.0, 1, 0, 1, 3), ("U2", 35.046, 135.002, 1, 0, 1, 3)]
    _write(tmp_path, users, [("S1", 5, 3)], [(DATE, "S1", "08:00", "17:00")],
           [("C1", 4, 0, "")], [("P1", "09:00", "12:00"), ("P2", "13:00", "16:00")],
           [(DATE, "U1", "P1"), (DATE, "U2", "P2")])
    cfg = RoutingConfig(assign_time_limit_sec=10)
    day = load_day(tmp_path, DATE)
    prov = _provider(day, cfg)
    plan = plan_day(day, prov, cfg)
    assert plan.dropped == {}
    chains = [p for p in plan.planned if p.route.is_chain]
    assert len(chains) == 1
    c = chains[0]
    assert c.route.stops == ["U1", "U2"] and c.end == hm_to_min("13:00")
    a, b = c.idle
    assert 0 <= b - a <= cfg.chain_max_wait
    assert validate(day, plan.planned, prov, cfg) == []
    # 連結なしの設定では使わない
    cfg2 = RoutingConfig(assign_time_limit_sec=10, chain_enabled=False)
    plan2 = plan_day(day, prov, cfg2)
    assert not any(p.route.is_chain for p in plan2.planned)
    # 連結なしだと乗せられない人が出るか、走行時間が長くなる
    assert plan2.dropped or plan.total_travel < plan2.total_travel


def test_lunch_can_be_taken_during_chain_wait():
    from shuttle_opt.routing import lunch
    from shuttle_opt.routing.data import Staff
    from shuttle_opt.routing.route_gen import PlannedRoute, Route
    cfg = RoutingConfig()
    st = Staff("S1", 3, 3, hm_to_min("08:00"), hm_to_min("17:00"))
    # 11:20 出発の送り（11:50 まで）→ 空車待機 11:50〜12:40 → 迎え 12:40〜13:00 施設着
    r = Route("c", "w1", "chain", ["U1", "U2"], [10], 100, 1, 0, 1, 40,
              wave2="w2", n_first=1, fixed_end=hm_to_min("13:00"), idle_from=30, tail_len=20, tail_offsets=[-20])
    p = PlannedRoute(r, "S1", "C1", hm_to_min("11:20"))
    assert p.idle == (hm_to_min("11:50"), hm_to_min("12:40"))
    br = lunch.find_break(st, [p], cfg)
    assert br == (hm_to_min("11:50"), hm_to_min("12:40"))
