"""Excel → CSV 変換のテスト（送迎テンプレートと同じレイアウトの架空データ）."""
from datetime import datetime, time

import pandas as pd
import pytest
from openpyxl import Workbook

from shuttle_opt.routing.config import RoutingConfig
from shuttle_opt.routing.data import load_day
from shuttle_opt.routing.excel_import import ImportErrorJa, convert
from shuttle_opt.routing.pipeline import plan_day
from shuttle_opt.routing.travel_time import DEPOT, HaversineProvider
from shuttle_opt.routing.validate import validate

D1, D2 = datetime(2026, 12, 1), datetime(2026, 12, 2)


def make_book(path, ng="P0003/P0004"):
    wb = Workbook()
    ws = wb.active
    ws.title = "月次シフト"
    ws.append([None, 46327] + [None] * 16 + [D1, D2, None, "公休"])
    ws.append([])
    ws.append([None, None, "職員ID", "生成対象", "職員区分", "性別", "勤務開始時間", "勤務終了時間",
               "優先順位", "介助量"] + [None] * 8 + ["Tue", "Wed"])
    ws.append([1, "職員A", "0100", "対象", "常勤", "女性", "8：00", "17：00", "３", "３"] + [None] * 8 + ["日", "日"])
    ws.append([2, "職員B", "0101", "対象", "常勤", "男性", time(7, 30), "16：30", "２", "２"] + [None] * 8 + ["日", "〇"])
    ws.append([3, "職員C", "0102", "対象", "パート", "女性", "8：00", "12：30", "１", "１"] + [None] * 8 + ["A", "有"])
    ws.append([4, "職員D", "0103", "対象外", "常勤", "女性", "8：00", "17：00", "３", "３"] + [None] * 8 + ["日", "日"])
    ws.append([5, "職員E", "0104", "対象", "常勤", "女性", "8：00", "17：00", "３", "３"] + [None] * 8 + ["謎", "早1"])

    ws = wb.create_sheet("車両情報")
    ws.append([None, None, "ナンバー", "送迎可能人数", "歩行器積載", "シート高", "燃費"])
    ws.append([1, "軽A", None, "３", None])
    ws.append([2, "軽B", None, "３", "１"])
    ws.append([3, "軽C", None, "３", "１"])
    ws.append([4, None, None, None, None])

    ws = wb.create_sheet("利用者情報")
    ws.append(["日付", "曜日", "利用者ＩＤ", "名前", "緯度", "経度", "利用時間帯", "乗車必要時間", "持参品",
               "介助", "組み合わせ禁止", None, "備考"])
    rows = [
        ("P0001", 35.004, 135.003, 1, None, None, None),
        ("P0002", 35.008, 135.001, 1, 10, "歩行器", "中"),
        ("P0003", 35.006, 134.998, 1, None, None, "重"),
        ("P0004", 35.002, 135.006, 2, None, None, None),
        ("P0005", 34.996, 135.002, 2, 5, None, None),
    ]
    for d in (D1, D2):
        for uid, lat, lng, plan, svc, item, care in rows:
            if d == D2 and uid == "P0001":
                lat += 0.001                       # 日によって座標が違う
            if d == D2 and uid == "P0005":
                item, care = "歩行器", "中"            # 日によって持参品・介助が違う
            ws.append([d, "Tue", uid, "氏名ダミー", lat, lng, plan, svc, item, care,
                       ng if uid == "P0003" else None, None, "備考ダミー（読まれないこと）"])

    ws = wb.create_sheet("送迎情報")
    ws.append(["ID", "プラン名", "到着時間", "出発時間"])
    ws.append([1, None, time(9, 0), time(12, 0)])
    ws.append([2, None, time(13, 0), time(15, 0)])

    ws = wb.create_sheet("勤務パターンマスター")
    ws.append(["パターン", "記号", "名称", "出退勤時間", None, "休憩"])
    ws.append([1, "日", "日勤", 830, 1730, 100])
    ws.append([5, "A", "AM勤務", 830, 1230, 0])
    ws.append([7, "早1", "早出1", 700, 1600, 100])
    ws.append([99901, "有", "有休", None, None, None])
    wb.save(path)


@pytest.fixture
def converted(tmp_path):
    xlsx = tmp_path / "template.xlsx"
    make_book(xlsx)
    out = tmp_path / "real"
    warns = convert(xlsx, out, facility=(35.0, 135.0), log=lambda *_: None)
    return out, warns


def _csv(p):
    return pd.read_csv(p, dtype=str, encoding="utf-8-sig").fillna("")


def test_staff_and_shifts(converted):
    out, warns = converted
    staff = _csv(out / "staff.csv")
    assert list(staff["staff_id"]) == ["0100", "0101", "0102", "0104"]      # 対象外は除く・先頭の0を保持
    assert list(staff["priority"]) == ["3", "2", "1", "3"]
    sh = _csv(out / "shifts.csv")
    got = {(r.date, r.staff_id): (r.start, r.end) for r in sh.itertuples()}
    assert got[("2026-12-01", "0100")] == ("08:00", "17:00")      # 日 = 個人の勤務時間
    assert got[("2026-12-01", "0101")] == ("07:30", "16:30")
    assert ("2026-12-02", "0101") not in got                        # 〇 = 休み
    assert got[("2026-12-01", "0102")] == ("08:30", "12:30")        # A = パターンマスター
    assert ("2026-12-02", "0102") not in got                        # 有 = 休暇
    assert ("2026-12-01", "0104") not in got                        # 不明な記号は休み扱い + 警告
    assert got[("2026-12-02", "0104")] == ("07:00", "16:00")
    assert any("謎" in w for w in warns)


def test_cars_plans(converted):
    out, _ = converted
    cars = _csv(out / "cars.csv")
    assert list(cars["car_id"]) == ["軽A", "軽B", "軽C"]
    assert list(cars["capacity"]) == ["3", "3", "3"]
    assert list(cars["walker_slots"]) == ["0", "1", "1"]
    plans = _csv(out / "plans.csv")
    assert plans.values.tolist() == [["1", "09:00", "12:00"], ["2", "13:00", "15:00"]]


def test_users_attendance_ng(converted):
    out, warns = converted
    users = _csv(out / "users.csv").set_index("user_id")
    assert users.loc["P0002", "care_need"] == "2" and users.loc["P0002", "walker"] == "1"
    assert users.loc["P0002", "service_min"] == "10"
    assert users.loc["P0003", "care_need"] == "3"
    assert users.loc["P0001", "service_min"] == ""                  # 空欄 → ソルバー側で既定5分
    assert float(users.loc["P0001", "lat"]) == pytest.approx(35.005)   # 最新日の座標に統一
    assert any("座標" in w and "P0001" in w for w in warns)
    # 介助・歩行器は日ごとの値で計算する
    d1, d2 = load_day(out, "2026-12-01"), load_day(out, "2026-12-02")
    assert (d1.users["P0005"].walker, d1.users["P0005"].care_need) == (0, 1)
    assert (d2.users["P0005"].walker, d2.users["P0005"].care_need) == (1, 2)
    assert d1.users["P0002"].service_min == 10 and d1.users["P0001"].service_min == 5
    att = _csv(out / "attendance.csv")
    assert len(att) == 10
    ng = _csv(out / "ng_pairs.csv")
    assert ng.values.tolist() == [["P0003", "P0004"]]
    # 氏名・備考は出力されない
    text = "".join(p.read_text(encoding="utf-8-sig") for p in out.glob("*.csv"))
    assert "氏名ダミー" not in text and "備考ダミー" not in text and "職員A" not in text


def test_facility_kept_when_not_given(tmp_path):
    xlsx = tmp_path / "t.xlsx"
    make_book(xlsx)
    out = tmp_path / "real"
    convert(xlsx, out, log=lambda *_: None)
    assert _csv(out / "facility.csv").empty                         # 未設定のひな形
    pd.DataFrame([{"lat": 35.0, "lng": 135.0}]).to_csv(out / "facility.csv", index=False)
    convert(xlsx, out, log=lambda *_: None)
    assert _csv(out / "facility.csv").values.tolist() == [["35.0", "135.0"]]   # 上書きしない
    with pytest.raises(ValueError, match="施設の緯度経度"):
        (out / "facility.csv").write_text("lat,lng\n", encoding="utf-8")
        load_day(out, "2026-12-01")


def test_missing_sheet(tmp_path):
    wb = Workbook()
    wb.save(tmp_path / "x.xlsx")
    with pytest.raises(ImportErrorJa):
        convert(tmp_path / "x.xlsx", tmp_path / "o", log=lambda *_: None)


def test_converted_data_solves_with_ng(converted):
    """変換したCSVでそのまま計画でき、同乗NGが守られること."""
    out, _ = converted
    cfg = RoutingConfig(assign_time_limit_sec=10)
    day = load_day(out, "2026-12-01")
    assert day.ng_pairs == {frozenset({"P0003", "P0004"})}
    pts = {DEPOT: day.facility, **{u.user_id: (u.lat, u.lng) for u in day.users.values()}}
    prov = HaversineProvider(pts, cfg.detour, cfg.speed_kmh)
    plan = plan_day(day, prov, cfg)
    assert plan.ok
    assert validate(day, plan.planned, prov, cfg, allow_dropped=plan.dropped) == []


def make_book_v2(path):
    """2026年11月版のレイアウト: シート名「○月_シフト」、B1 に年月の日付、直行・直帰・座標の列、
    利用者情報は利用時間帯の代わりに施設着・施設発、車名に「ヘルプ」を含むヘルプ車."""
    wb = Workbook()
    ws = wb.active
    ws.title = "12月_シフト"
    ws.append([None, datetime(2026, 12, 1)] + [None] * 16 + [D1, D2])
    ws.append([])
    ws.append([None, None, "職員ID", "生成対象", "職員区分", "性別", "勤務開始時間", "勤務終了時間",
               "優先順位", "介助量", "直行", "直帰", "座標", "座標"] + [None] * 4 + ["火", "水"])
    ws.append([1, "職員A", "0100", "対象", "常勤", "女性", "8:00", "17:00", "3", "3", "１", "１",
               35.02, 135.01] + [None] * 4 + ["日", "日"])
    ws.append([2, "職員B", "0101", "対象", "パート", "男性", "8:00", "17:00", "3", "3", None, None,
               None, None] + [None] * 4 + ["日", "日"])
    ws.append([3, "職員C", "0102", "対象", "常勤", "女性", "8:00", "17:00", "3", "3", "１", None,
               None, None] + [None] * 4 + ["日", "〇"])          # 直行だが座標なし → 施設発着

    ws = wb.create_sheet("車両情報")
    ws.append([None, None, "ナンバー", "送迎可能人数", "歩行器積載", "シート高", "燃費"])
    ws.append([1, "軽A", None, "4", "1"])
    ws.append([2, "ヘルプ1号", None, "4", "1"])

    ws = wb.create_sheet("利用者情報")
    ws.append(["日付", "曜日", "利用者ＩＤ", "名前", "緯度", "経度", "施設着", "施設発", "乗車必要時間", "持参品",
               "介助", "組み合わせ禁止"])
    for d in (D1, D2):
        ws.append([d, "火", "P0001", "氏名ダミー", 35.004, 135.003, time(9, 0), time(12, 0), None, None, None, None])
        ws.append([d, "火", "P0002", "氏名ダミー", 35.008, 135.001, time(13, 0), time(16, 30), 5, "歩行器", "中", None])
        ws.append([d, "火", "P0003", "氏名ダミー", 35.006, 134.998, time(14, 30), time(16, 30), None, None, None, None])

    ws = wb.create_sheet("送迎情報")
    ws.append(["ID", "プラン名", "到着時間", "出発時間"])
    ws.append([1, None, time(9, 0), time(12, 0)])

    ws = wb.create_sheet("希望休_設定")
    ws.append(["希望休の設定"])
    ws.append(["対象シート名", "12月_シフト"])

    ws = wb.create_sheet("勤務パターンマスター")
    ws.append(["パターン", "記号", "名称", "出退勤時間", None, "休憩"])
    ws.append([1, "日", "日勤", 800, 1700, 100])
    wb.save(path)


def test_new_layout_2026_11(tmp_path):
    xlsx = tmp_path / "template_v2.xlsx"
    make_book_v2(xlsx)
    out = tmp_path / "real"
    warns = convert(xlsx, out, facility=(35.0, 135.0), log=lambda *_: None)
    assert not any("シフト記号" in w for w in warns)               # B列の氏名をシフトとして読まない
    assert any("0102" in w and "座標がない" in w for w in warns)

    staff = _csv(out / "staff.csv").set_index("staff_id")
    assert staff.loc["0100", ["direct_start", "direct_end"]].tolist() == ["1", "1"]
    assert (float(staff.loc["0100", "home_lat"]), float(staff.loc["0100", "home_lng"])) == (35.02, 135.01)
    assert staff.loc["0102", ["direct_start", "direct_end"]].tolist() == ["0", "0"]
    assert staff.loc["0101", "employment"] == "パート"
    assert _csv(out / "cars.csv").set_index("car_id")["help"].to_dict() == {"軽A": "0", "ヘルプ1号": "1"}

    att = _csv(out / "attendance.csv")
    assert set(zip(att["user_id"], att["arrive"], att["depart"])) == {
        ("P0001", "09:00", "12:00"), ("P0002", "13:00", "16:30"), ("P0003", "14:30", "16:30")}

    cfg = RoutingConfig(assign_time_limit_sec=10)
    day = load_day(out, "2026-12-01", cfg)
    assert {w.wave_id for w in day.waves} == {"迎え09:00", "送り12:00", "迎え13:00", "迎え14:30", "送り16:30"}
    assert day.staff["0100"].direct_start and day.staff["0100"].home == (35.02, 135.01)
    assert day.staff["0100"].overtime_max == cfg.overtime_max      # 常勤・17時終わり
    assert day.staff["0101"].overtime_max == 0                     # パートは勤務が長くても残業なし
    assert day.users["P0002"].walker == 1 and day.users["P0002"].care_need == 2
    assert day.cars["ヘルプ1号"].help
    prov = HaversineProvider(day.points())
    plan = plan_day(day, prov, cfg)
    assert plan.ok and plan.dropped == {}
    assert validate(day, plan.planned, prov, cfg) == []
