"""コマンドライン実行: python -m shuttle_opt.main --data data/sample --out output"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .data_loader import load_problem
from .model import SolveParams, build_and_solve


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="送迎シフト最適化 (OR-Tools CP-SAT)")
    p.add_argument("--data", default="data/sample", help="入力CSVフォルダ")
    p.add_argument("--out", default="output", help="出力フォルダ")
    p.add_argument("--time-limit", type=float, default=30.0, help="求解時間上限 [秒]")
    p.add_argument("--max-consecutive", type=int, default=5, help="最大連続勤務日数")
    args = p.parse_args(argv)

    data = load_problem(args.data)
    params = SolveParams(time_limit_sec=args.time_limit, max_consecutive_days=args.max_consecutive)
    res = build_and_solve(data, params)

    print(f"status   : {res.status}")
    if res.objective is None:
        print("解が見つかりませんでした。")
        return 1
    print(f"objective: {res.objective:.0f}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 枠ごとの担当者
    rows = [{"date": t, "slot": s, "drivers": " ".join(ds), "count": len(ds)}
            for (t, s), ds in sorted(res.assignment.items())]
    df_assign = pd.DataFrame(rows)
    df_assign.to_csv(out / "assignment.csv", index=False, encoding="utf-8-sig")

    # ドライバー×枠 のシフト表 (○ = 担当)
    cols = [f"{t} {s}" for (t, s) in sorted(res.assignment)]
    table = pd.DataFrame("", index=sorted(res.shifts_per_driver), columns=cols)
    for (t, s), ds in res.assignment.items():
        for did in ds:
            table.loc[did, f"{t} {s}"] = "○"
    table["合計"] = pd.Series(res.shifts_per_driver)
    table.to_csv(out / "shift_table.csv", encoding="utf-8-sig")

    print("\n担当回数:", res.shifts_per_driver)
    if res.shortages:
        print("\n[警告] 人員不足の枠があります:")
        for sh in res.shortages:
            print(f"  {sh['date']} {sh['slot']}: 不足 {sh['short']} 名 / 車椅子対応不足 {sh['short_wheelchair']} 名")
    else:
        print("\n全枠の必要人数を満たしました。")
    print(f"\n出力: {out / 'assignment.csv'}, {out / 'shift_table.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
