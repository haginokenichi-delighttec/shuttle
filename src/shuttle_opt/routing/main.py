"""送迎ルート最適化 CLI.

    # attendance.csv にあるすべての日を計画
    python -m shuttle_opt.routing.main --data data/sample_routing --out output/routing

    # 日付を指定（複数可）
    python -m shuttle_opt.routing.main 2026-11-09 2026-11-10 --early 45

出力（--out 配下）
    routes_<date>.csv     ルート単位（職員・車・便・出発・帰着・人数・順路・GoogleマップURL・確認用URL）
    stops_<date>.csv      停車地単位（訪問順・到着予定時刻）
    timeline_<date>.txt   職員ごとのタイムライン
    summary.csv           日ごとの結果一覧（複数日を計画したとき）
    logs/run_<日時>.log   実行中に画面に出た内容（デバッグ用）
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import RoutingConfig
from .data import list_dates, load_day, min_to_hm, norm_date
from .maps import route_url
from .pipeline import describe, plan_day
from .travel_time import DEPOT, CachedMatrixProvider, HaversineProvider
from .validate import validate


class _Tee:
    """画面とログファイルの両方に書く（書くたびに flush して途中経過も残す）."""

    def __init__(self, stream, f):
        self.stream, self.f = stream, f

    def write(self, s):
        self.stream.write(s)
        self.stream.flush()
        self.f.write(s)
        self.f.flush()
        return len(s)

    def flush(self):
        self.stream.flush()
        self.f.flush()


def run_day(date: str, args, cfg: RoutingConfig, out: Path) -> dict:
    """1日分を計画して出力する. 戻り値は summary.csv の1行."""
    day = load_day(args.data, date)
    points = {DEPOT: day.facility, **{u.user_id: (u.lat, u.lng) for u in day.users.values()}}
    provider = HaversineProvider(points, cfg.detour, cfg.speed_kmh)
    matrix = args.matrix
    if matrix is None and not args.no_matrix and (Path(args.data) / "travel_matrix.csv").exists():
        matrix = Path(args.data) / "travel_matrix.csv"   # Google API で取得した表があれば自動で使う
    if matrix:
        provider = CachedMatrixProvider(matrix, fallback=provider)
        print(f"移動時間: {matrix}（表にない組は直線距離から推計）")
    else:
        print(f"移動時間: 直線距離 × {cfg.detour} ÷ 時速{cfg.speed_kmh:g}km で推計")

    print(f"対象日 {day.date}: 職員 {len(day.staff)} 名 / 使える車 {len(day.cars)} 台 / "
          f"利用者 {len(day.users)} 名 / 便 {len(day.waves)}")
    plan = plan_day(day, provider, cfg)
    print("\n".join(plan.log))
    summary = {"日付": day.date, "職員": len(day.staff), "車": len(day.cars), "利用者": len(day.users),
               "状態": plan.status, "ルート数": len(plan.planned), "総走行分": plan.total_travel,
               "乗せられない件数": sum(len(v) for v in plan.dropped.values()), "検証": ""}
    if not plan.ok:
        print("\n[ERROR] 計画が見つかりませんでした。")
        summary["検証"] = "計画なし"
        return summary

    errs = validate(day, plan.planned, provider, cfg, allow_dropped=plan.dropped)
    if errs:
        print("[検証NG]")
        print("\n".join("  " + e for e in errs))
        summary["検証"] = f"NG {len(errs)}件"
    else:
        print("検証OK: すべてのルールを満たしています")
        summary["検証"] = "OK"

    rows, stops = [], []
    for p in plan.planned:
        r = p.route
        rows.append({"route_id": r.route_id, "便": r.wave_id, "職員": p.staff_id, "車": p.car_id,
                     "施設出発": min_to_hm(p.start), "施設帰着": min_to_hm(p.end),
                     "人数": r.seats, "歩行器": r.walkers, "車椅子": r.wheelchairs, "最大介助": r.max_need,
                     "走行分": r.travel, "順路": " → ".join(r.stops),
                     "GoogleマップURL": route_url(pts := [(day.users[u].lat, day.users[u].lng) for u in r.stops],
                                                  day.facility),
                     "確認用URL": route_url(pts, day.facility, preview=True)})
        for i, (u, t) in enumerate(zip(r.stops, p.eta), start=1):
            stops.append({"route_id": r.route_id, "職員": p.staff_id, "車": p.car_id, "順番": i,
                          "利用者": u, "到着予定": min_to_hm(t)})
    pd.DataFrame(rows).to_csv(out / f"routes_{day.date}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(stops).to_csv(out / f"stops_{day.date}.csv", index=False, encoding="utf-8-sig")
    text = describe(day, plan)
    (out / f"timeline_{day.date}.txt").write_text(text, encoding="utf-8")
    print(f"\n総走行時間 {plan.total_travel} 分 / ルート {len(plan.planned)} 本\n")
    print(text)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="送迎ルート最適化")
    ap.add_argument("dates", nargs="*", help="対象日 YYYY-MM-DD（省略時は attendance.csv にある全日）")
    ap.add_argument("--date", action="append", default=[], help="対象日（dates と同じ。複数指定可）")
    ap.add_argument("--data", default="data/sample_routing")
    ap.add_argument("--out", default="output/routing")
    ap.add_argument("--matrix", default=None,
                    help="移動時間の表CSV（from_id,to_id,minutes）。省略時は <data>/travel_matrix.csv があれば使う")
    ap.add_argument("--no-matrix", action="store_true", help="移動時間の表を使わず直線距離で推計する")
    ap.add_argument("--early", type=int, default=None, help="迎えの早着許容（分）")
    ap.add_argument("--late", type=int, default=None, help="送りの出発遅れ許容（分）")
    ap.add_argument("--time-limit", type=int, default=None, help="Stage B の計算時間上限（秒）")
    args = ap.parse_args(argv)

    cfg = RoutingConfig()
    if args.early is not None:
        cfg.early_eps = args.early
    if args.late is not None:
        cfg.late_eps = args.late
    if args.time_limit is not None:
        cfg.assign_time_limit_sec = args.time_limit

    out = Path(args.out)
    log_dir = out / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"
    with open(log_path, "w", encoding="utf-8") as f:
        orig_out, orig_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(orig_out, f), _Tee(orig_err, f)
        try:
            print(f"開始 {datetime.now():%Y-%m-%d %H:%M:%S}  ログ: {log_path}")
            print(f"コマンド引数: {' '.join(argv if argv is not None else sys.argv[1:])}")
            print(f"設定: {cfg}\n")
            return _run(args, cfg, out)
        except Exception:
            traceback.print_exc()
            return 1
        finally:
            print(f"終了 {datetime.now():%Y-%m-%d %H:%M:%S}")
            sys.stdout, sys.stderr = orig_out, orig_err


def _run(args, cfg: RoutingConfig, out: Path) -> int:
    dates = [norm_date(d) for d in args.dates + args.date] or list_dates(args.data)
    if not dates:
        print("[ERROR] attendance.csv に日付がありません。")
        return 1
    print(f"計画する日: {', '.join(dates)}（{len(dates)} 日）\n")
    summaries = []
    for i, d in enumerate(dates, start=1):
        print(f"{'=' * 20} [{i}/{len(dates)}] {d} {'=' * 20}")
        try:
            summaries.append(run_day(d, args, cfg, out))
        except Exception as e:   # 1日失敗しても他の日は続ける
            print(f"[ERROR] {d}: {e}")
            summaries.append({"日付": d, "状態": "エラー", "検証": str(e)})
        print()

    df = pd.DataFrame(summaries)
    if len(dates) > 1:
        df.to_csv(out / "summary.csv", index=False, encoding="utf-8-sig")
    print("=" * 20, "まとめ", "=" * 20)
    print(df.to_string(index=False))
    print(f"\n出力: {out}")
    ok = all(s.get("検証") == "OK" for s in summaries)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
