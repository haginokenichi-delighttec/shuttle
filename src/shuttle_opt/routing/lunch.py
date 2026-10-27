"""昼休み.

ルール（config.py の lunch_* で変更可）
    - 11:30〜13:30 の間に、連続 30 分以上の休憩を必ず取れるようにする（その間は送迎しない）
    - 休憩は最大 60 分
    - 対象は、勤務時間が 11:30〜13:30 をすべて含む職員（午前だけ・午後だけの勤務の人は対象外）
    - 「送迎していない時間」は、ルートの出発から帰着後の準備時間（turnaround）が終わるまで以外
    - 連結ルート（送り→施設に戻らず次の迎え）の途中で、利用者を乗せずに待機している時間も休憩にできる

最適化（assign.py）では「30分の休憩枠」を職員ごとに1つ選ばせ、ルートと重ならないようにする。
出力用の休憩時間は、確定した計画からこのモジュールで求める（空き時間の中で最大60分）。
"""
from __future__ import annotations

from .config import RoutingConfig
from .data import DayProblem, Staff, hm_to_min
from .route_gen import PlannedRoute


def window(cfg: RoutingConfig) -> tuple[int, int]:
    return hm_to_min(cfg.lunch_start), hm_to_min(cfg.lunch_end)


def applies(st: Staff, cfg: RoutingConfig) -> bool:
    """昼休みの対象か（勤務時間が昼休みの時間帯をすべて含む）."""
    if not cfg.lunch_enabled:
        return False
    ws, we = window(cfg)
    return st.start <= ws and st.end >= we


def break_starts(cfg: RoutingConfig) -> list[int]:
    """30分の休憩枠の開始時刻の候補."""
    ws, we = window(cfg)
    out = list(range(ws, we - cfg.lunch_min + 1, cfg.start_step))
    if out and out[-1] != we - cfg.lunch_min:
        out.append(we - cfg.lunch_min)
    return out


def find_break(st: Staff, planned: list[PlannedRoute], cfg: RoutingConfig) -> tuple[int, int] | None:
    """確定した計画から休憩時間を決める. 取れなければ None.

    昼休みの時間帯の中で、送迎していない連続時間のうち最も長いものを選び、最大 lunch_max 分にする。
    """
    ws, we = window(cfg)
    busy = sorted(part for p in planned if p.staff_id == st.staff_id
                  for part in p.route.busy_parts(p.start, cfg.turnaround_min))
    best: tuple[int, int] | None = None
    t = ws
    for s, e in busy + [(we, we)]:
        s, e = min(max(s, ws), we), max(min(e, we), ws)
        if s > t and (best is None or s - t > best[1] - best[0]):
            best = (t, s)
        t = max(t, e)
    if best is None or best[1] - best[0] < cfg.lunch_min:
        return None
    return best[0], min(best[1], best[0] + cfg.lunch_max)


def breaks(day: DayProblem, planned: list[PlannedRoute], cfg: RoutingConfig) -> dict[str, tuple[int, int] | None]:
    return {sid: find_break(st, planned, cfg) for sid, st in day.staff.items() if applies(st, cfg)}
