"""送迎表ビューア（viewer/送迎表.html）用の当日データを書き出す.

    view_<date>.json
        当日の条件（職員のシフト・車・利用者の属性・便・同乗NG・設定値）と
        施設＋当日の利用者すべての組み合わせの移動時間（分）。
        ブラウザで便を編集したときに、ソルバーと同じ計算で時刻を出し直すために使う。

氏名は含めない（氏名は data/real/names.csv をブラウザが手元で読み込む）。
座標を含むため、出力ファイルは個人情報として扱うこと。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .config import RoutingConfig
from .data import DayProblem, hm_to_min
from .travel_time import DEPOT, TravelTimeProvider


def export_view(day: DayProblem, provider: TravelTimeProvider, cfg: RoutingConfig, out: Path) -> Path:
    ids = [DEPOT] + sorted(day.users)
    matrix = [[provider.minutes(a, b) for b in ids] for a in ids]
    data = {
        "date": day.date,
        "facility": list(day.facility),
        "config": {k: v for k, v in asdict(cfg).items() if isinstance(v, (int, float))},
        # 昼休みの時間帯（分）。ビューアが lunch.py と同じ方法で昼休みを計算する
        "lunch": {"start": hm_to_min(cfg.lunch_start), "end": hm_to_min(cfg.lunch_end)},
        # max_end: 残業を含めて施設に戻ってよい最終時刻（ビューアの勤務時間チェック用）
        "staff": [{"id": s.staff_id, "priority": s.priority, "care_skill": s.care_skill,
                   "start": s.start, "end": s.end, "max_end": s.work_end, "fulltime": s.fulltime}
                  for s in day.staff.values()],
        "cars": [{"id": c.car_id, "capacity": c.capacity, "walker_slots": c.walker_slots,
                  "wheelchair_slots": c.wheelchair_slots, "owner": c.owner, "help": c.help}
                 for c in day.cars.values()],
        "users": {u.user_id: {"lat": u.lat, "lng": u.lng, "care_need": u.care_need, "walker": u.walker,
                              "wheelchair": u.wheelchair, "seats": u.seats, "service_min": u.service_min,
                              "arrive": u.arrive, "depart": u.depart} for u in day.users.values()},
        "waves": [{"id": w.wave_id, "kind": w.kind, "time": w.time,
                   "users": list(w.user_ids)} for w in day.waves],
        "ng_pairs": [sorted(p) for p in day.ng_pairs],
        "matrix": {"ids": ids, "minutes": matrix},
    }
    path = out / f"view_{day.date}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path
