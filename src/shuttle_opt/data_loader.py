"""入力データ (CSV) の読み込み.

個人情報保護のため、入力データにはドライバー・利用者の氏名や病名などを含めず、
匿名ID (例: D01, U001) のみを使用してください。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

SLOTS = ("AM", "PM")


@dataclass
class Driver:
    driver_id: str
    max_shifts: int
    wheelchair_ok: bool


@dataclass
class Demand:
    date: str
    slot: str
    required: int
    required_wheelchair: int = 0


@dataclass
class ProblemData:
    drivers: list[Driver]
    demands: list[Demand]
    # (driver_id, date, slot) の集合。slot="ALL" は読み込み時に AM/PM に展開する
    unavailable: set[tuple[str, str, str]] = field(default_factory=set)

    @property
    def dates(self) -> list[str]:
        return sorted({d.date for d in self.demands})


def load_problem(data_dir: str | Path) -> ProblemData:
    data_dir = Path(data_dir)

    df_drv = pd.read_csv(data_dir / "drivers.csv", dtype={"driver_id": str})
    drivers = [
        Driver(r.driver_id, int(r.max_shifts), bool(int(r.wheelchair_ok)))
        for r in df_drv.itertuples(index=False)
    ]

    df_dem = pd.read_csv(data_dir / "demand.csv", dtype={"date": str, "slot": str})
    demands = [
        Demand(r.date, r.slot, int(r.required), int(getattr(r, "required_wheelchair", 0)))
        for r in df_dem.itertuples(index=False)
    ]

    unavailable: set[tuple[str, str, str]] = set()
    ua_path = data_dir / "unavailability.csv"
    if ua_path.exists():
        df_ua = pd.read_csv(ua_path, dtype=str)
        for r in df_ua.itertuples(index=False):
            slots = SLOTS if r.slot == "ALL" else (r.slot,)
            for s in slots:
                unavailable.add((r.driver_id, r.date, s))

    _validate(drivers, demands)
    return ProblemData(drivers, demands, unavailable)


def _validate(drivers: list[Driver], demands: list[Demand]) -> None:
    ids = [d.driver_id for d in drivers]
    if len(ids) != len(set(ids)):
        raise ValueError("drivers.csv に重複した driver_id があります")
    for dm in demands:
        if dm.slot not in SLOTS:
            raise ValueError(f"demand.csv の slot は {SLOTS} のいずれか: {dm.slot}")
        if dm.required_wheelchair > dm.required:
            raise ValueError(f"{dm.date} {dm.slot}: required_wheelchair が required を超えています")
