"""Google Routes API で移動時間の表（travel_matrix.csv）を作る.

使い方（Windows）:
    set GOOGLE_MAPS_API_KEY=取得したAPIキー
    fetch_matrix.bat --dry-run      … 何件・何回リクエストするかだけ確認（APIは呼ばない）
    fetch_matrix.bat                … 実行

出力（--data のフォルダ）:
    travel_matrix.csv          from_id,to_id,minutes,meters   （施設は DEPOT）
    travel_matrix_points.csv   取得時の座標（座標が変わった人は次回取り直す）

- 2回目以降は、まだ表にない人・座標が変わった人の分だけ取得する（--refresh で全件取り直し）。
- 1リクエストは 25×25＝625 件まで（Routes API の上限）に分割して送る。
- Routes API の既定の利用上限（1分あたり 3,000 件）を超えないよう、送信ペースを自動で調整する
  （既定は 1分あたり 2,800 件まで。--epm で変更可。上限を引き上げた場合はそれに合わせる）。
  そのため全件取得には数分かかる（約1.9万件なら7分前後）。
- 1リクエストごとにファイルへ追記するので、途中で止まってもそこまでの結果は残る。
- 経路が見つからなかった組は表に入れない（計画時は直線距離からの推計で補われる）。

注意: 利用者宅の座標を Google に送信します。施設の規程で問題ないことを確認してから使ってください。
API の利用料金は件数に応じてかかります。--dry-run で件数を確認してください。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.error
from collections import deque
import urllib.request
from pathlib import Path

ENDPOINT = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
FIELD_MASK = "originIndex,destinationIndex,duration,distanceMeters,status,condition"
DEPOT = "DEPOT"
BLOCK = 25   # 25 × 25 = 625 件 / リクエスト


# ---------------------------------------------------------------- 入出力
def load_points(data_dir: Path) -> dict[str, tuple[float, float]]:
    pts: dict[str, tuple[float, float]] = {}
    with open(data_dir / "facility.csv", encoding="utf-8-sig") as f:
        r = next(csv.DictReader(f))
        pts[DEPOT] = (float(r["lat"]), float(r["lng"]))
    with open(data_dir / "users.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            pts[r["user_id"].strip()] = (float(r["lat"]), float(r["lng"]))
    return pts


def load_cache(matrix: Path, points_file: Path) -> tuple[set[tuple[str, str]], dict[str, tuple[float, float]]]:
    done: set[tuple[str, str]] = set()
    if matrix.exists():
        with open(matrix, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                done.add((r["from_id"], r["to_id"]))
    old_pts: dict[str, tuple[float, float]] = {}
    if points_file.exists():
        with open(points_file, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                old_pts[r["id"]] = (float(r["lat"]), float(r["lng"]))
    return done, old_pts


# ---------------------------------------------------------------- 取得計画
def _chunks(xs: list[str], n: int) -> list[list[str]]:
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def plan_blocks(ids: list[str], stale: set[str], done: set[tuple[str, str]],
                block: int = BLOCK) -> list[tuple[list[str], list[str]]]:
    """取得が必要な (出発地リスト, 到着地リスト) の組を作る.

    stale = 座標が新しい／変わった地点。これに関わる組と、表にない組を取得する。
    """
    def needed(a: str, b: str) -> bool:
        return a != b and (a in stale or b in stale or (a, b) not in done)

    blocks = []
    for os_ in _chunks(ids, block):
        for ds in _chunks(ids, block):
            o_need = [a for a in os_ if any(needed(a, b) for b in ds)]
            if not o_need:
                continue
            d_need = [b for b in ds if any(needed(a, b) for a in o_need)]
            blocks.append((o_need, d_need))
    return blocks


# ---------------------------------------------------------------- API 呼び出し
def _wp(p: tuple[float, float]) -> dict:
    return {"waypoint": {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}}


def post_matrix(api_key: str, origins: list[tuple[float, float]], dests: list[tuple[float, float]],
                routing_preference: str, retries: int = 5) -> list[dict]:
    body = {
        "origins": [_wp(p) for p in origins],
        "destinations": [_wp(p) for p in dests],
        "travelMode": "DRIVE",
        "routingPreference": routing_preference,
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": api_key,
                 "X-Goog-FieldMask": FIELD_MASK})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                # 429 = 1分あたりの上限超過。1分待てば枠が空く
                wait = 61 if e.code == 429 else 2 ** attempt
                reason = "利用上限（1分あたりの件数）に達しました" if e.code == 429 else f"一時的なエラー {e.code}"
                print(f"  {reason}。{wait} 秒待って再試行します", flush=True)
                time.sleep(wait)
                continue
            if e.code == 429:
                raise RuntimeError("Routes API の利用上限に達しました（429 Quota exceeded）。"
                                   "1日あたりの上限の可能性もあります。Google Cloud の「割り当て」を確認してください。\n"
                                   + msg) from None
            raise RuntimeError(f"Routes API エラー {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"接続できません: {e.reason}") from None
    raise RuntimeError("再試行の上限に達しました")


def parse_elements(elements: list[dict], o_ids: list[str], d_ids: list[str]) -> tuple[list[list], int]:
    """戻り値: ([from_id, to_id, minutes, meters] のリスト, 経路なしの件数)."""
    rows, missing = [], 0
    for el in elements:
        oi, di = el.get("originIndex", 0), el.get("destinationIndex", 0)
        a, b = o_ids[oi], d_ids[di]
        if a == b:
            continue
        if el.get("status", {}).get("code") or el.get("condition") != "ROUTE_EXISTS" or "duration" not in el:
            missing += 1
            continue
        sec = float(str(el["duration"]).rstrip("s"))
        rows.append([a, b, max(1, math.ceil(sec / 60)), int(el.get("distanceMeters", 0))])
    return rows, missing


# ---------------------------------------------------------------- メイン
def main(argv: list[str] | None = None, post=post_matrix) -> int:
    ap = argparse.ArgumentParser(description="Google Routes API で移動時間の表を作る")
    ap.add_argument("--data", default="data/sample_routing", help="facility.csv と users.csv のあるフォルダ")
    ap.add_argument("--out", default=None, help="出力CSV（既定: <data>/travel_matrix.csv）")
    ap.add_argument("--refresh", action="store_true", help="既存の表を無視して全件取り直す")
    ap.add_argument("--traffic", action="store_true",
                    help="交通状況を考慮する（TRAFFIC_AWARE。既定は TRAFFIC_UNAWARE）")
    ap.add_argument("--dry-run", action="store_true", help="件数とリクエスト数だけ表示して終わる")
    ap.add_argument("--epm", type=int, default=2800,
                    help="1分あたりに送る件数の上限（Routes API の既定上限は 3,000）")
    args = ap.parse_args(argv)

    data = Path(args.data)
    out = Path(args.out) if args.out else data / "travel_matrix.csv"
    points_file = out.with_name(out.stem + "_points.csv")
    pts = load_points(data)
    ids = sorted(pts)

    done, old_pts = (set(), {}) if args.refresh else load_cache(out, points_file)
    stale = {i for i in ids if i not in old_pts or old_pts[i] != pts[i]}
    if args.refresh:
        stale = set(ids)
    blocks = plan_blocks(ids, stale, done)
    n_elem = sum(len(o) * len(d) for o, d in blocks)

    print(f"地点数: {len(ids)}（施設＋利用者 {len(ids) - 1} 名）")
    print(f"取得済み: {len(done)} 件 / 新規・座標変更の地点: {len(stale)}")
    print(f"今回の取得: {n_elem} 件（リクエスト {len(blocks)} 回、"
          f"1分あたり {args.epm} 件までのペースで約 {math.ceil(n_elem / max(1, args.epm))} 分）")
    if args.dry_run or not blocks:
        print("取得は行いませんでした。" if args.dry_run else "取得が必要な組はありません。")
        return 0

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        print("[ERROR] 環境変数 GOOGLE_MAPS_API_KEY が設定されていません。")
        print('        コマンドプロンプト: set GOOGLE_MAPS_API_KEY=取得したAPIキー')
        print('        PowerShell        : $env:GOOGLE_MAPS_API_KEY = "取得したAPIキー"')
        return 1
    pref = "TRAFFIC_AWARE" if args.traffic else "TRAFFIC_UNAWARE"

    # 座標が変わった地点の古い行は捨てて書き直す
    keep: list[list[str]] = []
    if out.exists() and not args.refresh:
        with open(out, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["from_id"] not in stale and r["to_id"] not in stale and r["from_id"] in pts and r["to_id"] in pts:
                    keep.append([r["from_id"], r["to_id"], r["minutes"], r.get("meters", "")])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["from_id", "to_id", "minutes", "meters"])
        w.writerows(keep)

    # 取得に使う座標を先に保存（途中で止まっても、次回は足りない組だけ取得できるように）
    with open(points_file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "lat", "lng"])
        for i in ids:
            w.writerow([i, pts[i][0], pts[i][1]])

    total_rows, total_missing = 0, 0
    sent: deque[tuple[float, int]] = deque()   # 直近1分間に送った (時刻, 件数)
    for k, (o_ids, d_ids) in enumerate(blocks, start=1):
        n = len(o_ids) * len(d_ids)
        while True:   # 1分あたりの上限を超えないように待つ
            now = time.monotonic()
            while sent and now - sent[0][0] >= 60:
                sent.popleft()
            if not sent or sum(c for _, c in sent) + n <= args.epm:
                break
            wait = 60 - (now - sent[0][0]) + 0.5
            print(f"  1分あたりの上限に合わせて {wait:.0f} 秒待機…", flush=True)
            time.sleep(wait)
        print(f"[{k}/{len(blocks)}] {len(o_ids)}×{len(d_ids)} 件を取得中…", flush=True)
        try:
            els = post(api_key, [pts[i] for i in o_ids], [pts[i] for i in d_ids], pref)
        except RuntimeError as e:
            print(f"\n[ERROR] {e}")
            print(f"ここまでの {total_rows} 件は保存済みです。もう一度実行すると、残りの組だけ取得します。")
            return 1
        sent.append((time.monotonic(), n))
        rows, missing = parse_elements(els, o_ids, d_ids)
        with open(out, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        total_rows += len(rows)
        total_missing += missing

    print(f"\n完了: {total_rows} 件を保存しました → {out}")
    if total_missing:
        print(f"[警告] 経路が見つからなかった組: {total_missing} 件（計画時は直線距離からの推計を使います）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
