"""入力CSVの読み込みと、当日分の問題データの組み立て.

個人情報保護のため、入力には匿名ID・緯度経度・属性値のみを使います
（氏名・住所・病名などは含めないこと）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


def norm_date(s: str) -> str:
    """2026/11/9, 2026-11-09 などを 2026-11-09 にそろえる."""
    return pd.to_datetime(str(s).strip()).strftime("%Y-%m-%d")


def _int(v, default: int = 0) -> int:
    """空欄は default."""
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return default
    return int(float(v))


def hm_to_min(s: str) -> int:
    h, m = str(s).strip().split(":")
    return int(h) * 60 + int(m)


def min_to_hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


DEPOT = "DEPOT"   # 施設の地点ID


def home_id(staff_id: str) -> str:
    """職員の自宅の地点ID（移動時間の表・URL で使う）."""
    return f"HOME_{staff_id}"


def _float(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    return float(v)


@dataclass
class Staff:
    staff_id: str
    priority: int
    care_skill: int
    start: int          # 当日のシフト開始（分）
    end: int
    home: tuple[float, float] | None = None   # 自宅の (緯度, 経度)
    direct_start: bool = False   # 直行: 朝は自宅から迎えに出る
    direct_end: bool = False     # 直帰: 夕方は送りの後そのまま自宅へ帰る


@dataclass
class Car:
    car_id: str
    capacity: int
    walker_slots: int
    owner: str | None   # 職員所有車なら staff_id
    wheelchair_slots: int = 0


@dataclass
class User:
    user_id: str
    lat: float
    lng: float
    care_need: int
    walker: int
    seats: int
    service_min: int
    plan_id: str
    wheelchair: int = 0   # 車椅子のまま乗車する（車椅子枠が必要）


@dataclass
class Wave:
    """便 = 方向 × 時刻. 例: pickup@09:00"""
    wave_id: str
    kind: str           # "pickup"（迎え） / "dropoff"（送り）
    time: int           # 迎え: 施設到着時刻, 送り: 施設出発時刻
    plan_id: str
    user_ids: list[str] = field(default_factory=list)


@dataclass
class DayProblem:
    date: str
    facility: tuple[float, float]
    staff: dict[str, Staff]
    cars: dict[str, Car]
    users: dict[str, User]
    waves: list[Wave]
    ng_pairs: set[frozenset[str]] = field(default_factory=set)   # 同じ車に乗せない利用者の組

    def points(self) -> dict[str, tuple[float, float]]:
        """地点ID → (緯度, 経度). 施設・当日の利用者・直行直帰する職員の自宅."""
        pts = {DEPOT: self.facility}
        pts.update({u.user_id: (u.lat, u.lng) for u in self.users.values()})
        for s in self.staff.values():
            if s.home and (s.direct_start or s.direct_end):
                pts[home_id(s.staff_id)] = s.home
        return pts

    def ng_ok(self, stops: list[str]) -> bool:
        """同乗NGの組が含まれていなければ True."""
        st = set(stops)
        return not any(pair <= st for pair in self.ng_pairs)

    def pairs(self) -> list[tuple[str, str]]:
        """当日の候補ペア (staff_id, car_id).

        - 職員所有車は持ち主だけが運転する
        - 車を所有する職員は自分の車だけを使う
        """
        owners = {c.owner for c in self.cars.values() if c.owner}
        out = []
        for s in self.staff.values():
            for c in self.cars.values():
                if c.owner and c.owner != s.staff_id:
                    continue
                if s.staff_id in owners and c.owner != s.staff_id:
                    continue
                out.append((s.staff_id, c.car_id))
        return out


def list_dates(data_dir: str | Path) -> list[str]:
    """attendance.csv に出てくる日付（昇順）."""
    df = pd.read_csv(Path(data_dir) / "attendance.csv", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return sorted({norm_date(d) for d in df["date"].dropna()})


def load_day(data_dir: str | Path, date: str) -> DayProblem:
    d = Path(data_dir)
    fac = pd.read_csv(d / "facility.csv").iloc[0]
    df_staff = pd.read_csv(d / "staff.csv", dtype={"staff_id": str})
    df_shift = pd.read_csv(d / "shifts.csv", dtype=str)
    df_cars = pd.read_csv(d / "cars.csv", dtype={"car_id": str, "owner_staff_id": str})
    df_users = pd.read_csv(d / "users.csv", dtype={"user_id": str})
    df_plans = pd.read_csv(d / "plans.csv", dtype=str)
    df_att = pd.read_csv(d / "attendance.csv", dtype=str)

    date = norm_date(date)
    for df in (df_staff, df_shift, df_cars, df_users, df_plans, df_att):
        df.columns = [c.strip() for c in df.columns]
    df_shift["date"] = df_shift["date"].map(norm_date)
    df_att["date"] = df_att["date"].map(norm_date)
    for df, cols in ((df_shift, ["staff_id"]), (df_att, ["user_id", "plan_id"]),
                     (df_plans, ["plan_id"]), (df_staff, ["staff_id"]), (df_users, ["user_id"])):
        for c in cols:
            df[c] = df[c].astype(str).str.strip()
    unknown = set(df_att["plan_id"]) - set(df_plans["plan_id"])
    if unknown:
        raise ValueError(f"attendance.csv に plans.csv にないプランID: {sorted(unknown)}")
    if df_users["user_id"].duplicated().any():
        raise ValueError(f"users.csv に重複した user_id: {sorted(df_users[df_users['user_id'].duplicated()]['user_id'].unique())}")

    shift = df_shift[df_shift["date"] == date].set_index("staff_id")
    staff = {}
    for r in df_staff.itertuples(index=False):
        if r.staff_id not in shift.index:
            continue   # 当日休み
        sh = shift.loc[r.staff_id]
        lat, lng = _float(getattr(r, "home_lat", None)), _float(getattr(r, "home_lng", None))
        home = (lat, lng) if lat is not None and lng is not None else None
        ds, de = bool(_int(getattr(r, "direct_start", 0))), bool(_int(getattr(r, "direct_end", 0)))
        if (ds or de) and home is None:
            raise ValueError(f"staff.csv: {r.staff_id} は直行/直帰ですが自宅座標（home_lat, home_lng）がありません")
        staff[r.staff_id] = Staff(r.staff_id, int(r.priority), int(r.care_skill),
                                  hm_to_min(sh["start"]), hm_to_min(sh["end"]), home, ds, de)

    cars = {}
    for r in df_cars.itertuples(index=False):
        owner = r.owner_staff_id if isinstance(r.owner_staff_id, str) and r.owner_staff_id else None
        if owner and owner not in staff:
            continue   # 持ち主が休みの日は、その車は使えない
        cars[r.car_id] = Car(r.car_id, int(r.capacity), _int(r.walker_slots), owner,
                             _int(getattr(r, "wheelchair_slots", 0)))

    att = df_att[df_att["date"] == date]
    if att.empty:
        raise ValueError(f"{date} の利用者がいません（attendance.csv）")
    plan_of = dict(zip(att["user_id"], att["plan_id"]))
    users = {}
    for r in df_users.itertuples(index=False):
        if r.user_id in plan_of:
            users[r.user_id] = User(r.user_id, float(r.lat), float(r.lng), _int(r.care_need, 1),
                                    _int(r.walker), _int(r.seats, 1), _int(r.service_min, 5),
                                    plan_of[r.user_id], _int(getattr(r, "wheelchair", 0)))
    missing = set(plan_of) - set(users)
    if missing:
        raise ValueError(f"users.csv に存在しない利用者ID: {sorted(missing)}")

    waves = []
    for p in df_plans.itertuples(index=False):
        uids = sorted(u for u, pid in plan_of.items() if pid == p.plan_id)
        if not uids:
            continue
        waves.append(Wave(f"迎え{p.arrive}_{p.plan_id}", "pickup", hm_to_min(p.arrive), p.plan_id, uids))
        waves.append(Wave(f"送り{p.depart}_{p.plan_id}", "dropoff", hm_to_min(p.depart), p.plan_id, uids))
    waves.sort(key=lambda w: w.time)

    ng: set[frozenset[str]] = set()
    if (d / "ng_pairs.csv").exists():
        df_ng = pd.read_csv(d / "ng_pairs.csv", dtype=str)
        df_ng.columns = [c.strip() for c in df_ng.columns]
        for r in df_ng.itertuples(index=False):
            a, b = str(r.user_a).strip(), str(r.user_b).strip()
            if a and b and a != b and a != "nan" and b != "nan":
                ng.add(frozenset((a, b)))

    return DayProblem(date, (float(fac["lat"]), float(fac["lng"])), staff, cars, users, waves, ng)
