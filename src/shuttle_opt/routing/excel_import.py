"""送迎テンプレート（Excel: .xlsm/.xlsx）→ ソルバー入力CSV の変換.

    python -m shuttle_opt.routing.excel_import 送迎テンプレート.xlsm --out data/real
    python -m shuttle_opt.routing.excel_import 送迎テンプレート.xlsm --facility 36.25,137.97

個人情報保護のため、次の列だけを読み込みます（氏名・性別・備考などの列は読みません）。
    シフト              職員ID, 生成対象, 職員区分（常勤/パートなど）, 勤務開始時間, 勤務終了時間, 優先順位, 介助量,
                        （あれば）直行, 直帰, 座標（自宅の緯度・経度の2列）, 日付ごとのシフト記号
                        シート名は「希望休_設定」の対象シート名 →「月次シフト」→「○月_シフト」の順に探す
    車両情報            車名（B列。「ヘルプ」を含む車はヘルプ車）, 送迎可能人数, 歩行器積載, （あれば）車椅子積載
    利用者情報          日付, 利用者ID, 緯度, 経度, 施設着, 施設発（旧形式は 利用時間帯）, 乗車必要時間, 持参品, 介助, 組み合わせ禁止
    送迎情報            ID, 到着時間, 出発時間（旧形式の 利用時間帯 を使うときだけ必要）
    勤務パターンマスター 記号, 出退勤時間

変換ルール
    シフト記号  「日」= その職員の勤務開始/終了時間、「〇」「○」・空欄 = 休み、
                それ以外 = 勤務パターンマスターの時刻（時刻のない記号は休み扱い）
    介助        空欄・軽 = 1、中 = 2、重 = 3
    持参品      「歩行器」を含む → 歩行器あり、「車椅子」を含む → 車椅子あり
    座標        日によって違う利用者は、最も新しい日の値に統一（警告を出す）
    介助・歩行器・車椅子・乗車必要時間
                日ごとの値を attendance.csv に出力（その日の値で計算する）
    施設の座標  Excel にないので --facility で指定するか、出力先の facility.csv に直接書く
                （facility.csv が既にあれば上書きしない）
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

OFF_SYMBOLS = {"", "〇", "○", "◯"}
CARE_MAP = {"": 1, "軽": 1, "中": 2, "重": 3, "1": 1, "2": 2, "3": 3}
NG_SPLIT = re.compile(r"[/／、,，・\s]+")


class ImportErrorJa(Exception):
    """変換を続けられないエラー."""


# ---------------------------------------------------------------- 値の正規化
def nz(v) -> str:
    """全角→半角などをそろえた文字列（空欄は ""）."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return unicodedata.normalize("NFKC", str(v)).strip()


def to_int(v, default: int | None = None) -> int | None:
    s = nz(v)
    if s == "":
        return default
    try:
        return int(float(s))
    except ValueError:
        return default


def to_hm(v) -> str | None:
    """8:00 / 8：00 / 08:00:00 / time型 / 830（パターンマスター形式）→ "08:00"."""
    if v is None or nz(v) == "":
        return None
    if isinstance(v, datetime):
        v = v.time()
    if isinstance(v, time):
        return f"{v.hour:02d}:{v.minute:02d}"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 0 <= v < 1:                       # Excel の時刻（1日=1.0）
            m = round(v * 24 * 60)
            return f"{m // 60:02d}:{m % 60:02d}"
        n = int(v)                           # 830 → 08:30
        return f"{n // 100:02d}:{n % 100:02d}"
    s = nz(v)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", s)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if s.isdigit():
        n = int(s)
        return f"{n // 100:02d}:{n % 100:02d}"
    return None


def to_date(v) -> str | None:
    if v is None or nz(v) == "":
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    try:
        return pd.to_datetime(nz(v)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def find_cols(header: tuple, names: dict[str, list[str]], required: list[str], sheet: str) -> dict[str, int]:
    """見出し名から列番号を探す（全角/半角・空白の違いは無視）."""
    norm = [nz(h).replace(" ", "") for h in header]
    out = {}
    for key, cands in names.items():
        for c in cands:
            if c in norm:
                out[key] = norm.index(c)
                break
    miss = [k for k in required if k not in out]
    if miss:
        raise ImportErrorJa(f"「{sheet}」シートに必要な見出しがありません: {', '.join(names[k][0] for k in miss)}")
    return out


def sheet_rows(wb, name: str) -> list[tuple]:
    if name not in wb.sheetnames:
        raise ImportErrorJa(f"シート「{name}」が見つかりません")
    return [tuple(r) for r in wb[name].iter_rows(values_only=True)]


def cell(r: tuple, i: int | None):
    return r[i] if i is not None and i < len(r) else None


# ---------------------------------------------------------------- 各シート
def read_patterns(wb) -> tuple[dict[str, tuple[str, str] | None], set[str]]:
    """勤務パターンマスター: 記号 → (開始, 終了). 時刻がない記号（休暇など）は None.

    戻り値の2つ目は、同じ記号で時刻が違う行がある記号（最初の行を使う）.
    """
    rows = sheet_rows(wb, "勤務パターンマスター")
    col = find_cols(rows[0], {"sym": ["記号"], "time": ["出退勤時間"]}, ["sym", "time"], "勤務パターンマスター")
    out: dict[str, tuple[str, str] | None] = {}
    dup = set()
    for r in rows[1:]:
        sym = nz(cell(r, col["sym"]))
        if not sym:
            continue
        st, en = to_hm(cell(r, col["time"])), to_hm(cell(r, col["time"] + 1))   # 出勤は見出しの列, 退勤はその右
        val = None
        if st and en and not (st == "00:00" and en == "00:00"):
            if en <= st:
                en = "24:00"                 # 日をまたぐ勤務は当日24:00までとして扱う
            val = (st, en)
        if sym in out and out[sym] != val:
            dup.add(sym)
            continue                         # 同じ記号が複数あるときは最初の行を使う
        out[sym] = val
    return out, dup


def shift_sheet_name(wb) -> str:
    """シフトのシート名. 「希望休_設定」の対象シート名 →「月次シフト」→「○月_シフト」の順に探す."""
    if "希望休_設定" in wb.sheetnames:
        for r in wb["希望休_設定"].iter_rows(max_row=20, values_only=True):
            if nz(cell(r, 0)) == "対象シート名" and nz(cell(r, 1)) in wb.sheetnames:
                return nz(cell(r, 1))
    if "月次シフト" in wb.sheetnames:
        return "月次シフト"
    for name in wb.sheetnames:
        if re.fullmatch(r"\d{1,2}月_?シフト", nz(name)):
            return name
    raise ImportErrorJa("シフトのシート（「月次シフト」または「○月_シフト」）が見つかりません")


def read_home(r: tuple, col: dict[str, int]) -> tuple[float, float] | None:
    """座標（2列: 緯度, 経度）. 片方でも空欄なら None. 90 を超える方を経度とみなす."""
    if "home" not in col:
        return None
    try:
        a, b = float(nz(cell(r, col["home"]))), float(nz(cell(r, col["home"] + 1)))
    except ValueError:
        return None
    return (b, a) if abs(a) > 90 else (a, b)


def read_staff(wb, patterns, dup, warn) -> tuple[list[dict], list[dict]]:
    name = shift_sheet_name(wb)
    rows = sheet_rows(wb, name)
    hdr_i = next((i for i, r in enumerate(rows[:20]) if "職員ID" in [nz(v) for v in r]), None)
    if hdr_i is None:
        raise ImportErrorJa(f"「{name}」シートに「職員ID」の見出しが見つかりません")
    col = find_cols(rows[hdr_i], {
        "id": ["職員ID"], "target": ["生成対象"], "start": ["勤務開始時間"], "end": ["勤務終了時間"],
        "priority": ["優先順位"], "skill": ["介助量"], "emp": ["職員区分"],
        "ds": ["直行"], "de": ["直帰"], "home": ["座標", "自宅座標"],
    }, ["id", "priority", "skill"], name)
    # 日付の見出し行（見出し行より上で日付が最も多い行）. 職員の属性の列より右だけを日付の列とする
    # （B1 などのタイトル欄に年月の日付が入っていても、氏名の列をシフトとして読まないように）
    first = max(col.values()) + (2 if "home" in col and col["home"] == max(col.values()) else 1)
    date_row = max(rows[:hdr_i], key=lambda r: sum(isinstance(v, (datetime, date)) for v in r[first:]))
    date_cols = {i: to_date(v) for i, v in enumerate(date_row) if i >= first and isinstance(v, (datetime, date))}
    if not date_cols:
        raise ImportErrorJa(f"「{name}」シートに日付の見出し行が見つかりません")

    staff, shifts, unknown, used_dup = [], [], defaultdict(int), set()
    for r in rows[hdr_i + 1:]:
        sid = nz(cell(r, col["id"]))
        if not sid:
            continue
        if "target" in col and nz(cell(r, col["target"])) not in ("対象", ""):
            continue
        own = (to_hm(cell(r, col.get("start"))), to_hm(cell(r, col.get("end"))))
        ds, de = to_int(cell(r, col.get("ds")), 0) == 1, to_int(cell(r, col.get("de")), 0) == 1
        home = read_home(r, col)
        if (ds or de) and home is None:
            warn(f"職員 {sid}: 直行・直帰ですが座標がないため、施設発着として扱います")
            ds = de = False
        staff.append({"staff_id": sid, "priority": to_int(cell(r, col["priority"]), 1),
                      "care_skill": to_int(cell(r, col["skill"]), 1),
                      "employment": nz(cell(r, col.get("emp"))),
                      "home_lat": home[0] if home else "", "home_lng": home[1] if home else "",
                      "direct_start": int(ds), "direct_end": int(de)})
        for i, d in date_cols.items():
            sym = nz(cell(r, i))
            if sym in OFF_SYMBOLS:
                continue
            if sym == "日" and own[0] and own[1]:
                st, en = own
            elif sym in patterns:
                if sym in dup:
                    used_dup.add(sym)
                if patterns[sym] is None:
                    continue                 # 休暇などの記号
                st, en = patterns[sym]
            else:
                unknown[sym] += 1
                continue
            shifts.append({"date": d, "staff_id": sid, "start": st, "end": en})
    if used_dup:
        warn(f"勤務パターンマスターに同じ記号が複数あり、最初の行の時刻を使いました: {', '.join(sorted(used_dup))}")
    if unknown:
        warn("勤務パターンマスターにないシフト記号は休み扱いにしました: "
             + ", ".join(f"「{k}」{v}件" for k, v in unknown.items()))
    if not staff:
        raise ImportErrorJa(f"「{name}」に生成対象の職員がいません")
    return staff, shifts


def read_cars(wb, warn) -> list[dict]:
    rows = sheet_rows(wb, "車両情報")
    col = find_cols(rows[0], {"cap": ["送迎可能人数"], "walker": ["歩行器積載"], "chair": ["車椅子積載"]},
                    ["cap"], "車両情報")
    cars, seen = [], set()
    for r in rows[1:]:
        name, cap = nz(cell(r, 1)), to_int(cell(r, col["cap"]))      # 車名は B列
        if not name or cap is None:
            continue
        cid = name if name not in seen else f"{name}_{nz(cell(r, 0))}"
        seen.add(cid)
        cars.append({"car_id": cid, "capacity": cap, "walker_slots": to_int(cell(r, col["walker"]), 0),
                     "owner_staff_id": "", "wheelchair_slots": to_int(cell(r, col.get("chair")), 0),
                     "help": int("ヘルプ" in name)})   # 車名に「ヘルプ」を含む車は、足りないときだけ使う
    if not cars:
        raise ImportErrorJa("「車両情報」に車がありません（車名と送迎可能人数が必要）")
    return cars


def read_plans(wb) -> list[dict]:
    """送迎情報（プランID → 到着・出発時刻）. 旧形式（利用時間帯）のときだけ使う. シートがなければ空."""
    if "送迎情報" not in wb.sheetnames:
        return []
    rows = sheet_rows(wb, "送迎情報")
    if not rows:
        return []
    col = find_cols(rows[0], {"id": ["ID"], "arrive": ["到着時間"], "depart": ["出発時間"]},
                    ["id", "arrive", "depart"], "送迎情報")
    plans = []
    for r in rows[1:]:
        pid = nz(cell(r, col["id"]))
        a, d = to_hm(cell(r, col["arrive"])), to_hm(cell(r, col["depart"]))
        if pid and a and d:
            plans.append({"plan_id": pid, "arrive": a, "depart": d})
    return plans


def read_users(wb, warn) -> tuple[list[dict], list[dict], list[dict]]:
    rows = sheet_rows(wb, "利用者情報")
    col = find_cols(rows[0], {
        "date": ["日付"], "id": ["利用者ID"], "lat": ["緯度"], "lng": ["経度"], "plan": ["利用時間帯"],
        "arrive": ["施設着"], "depart": ["施設発"],
        "service": ["乗車必要時間"], "item": ["持参品"], "care": ["介助"], "ng": ["組み合わせ禁止"],
    }, ["date", "id", "lat", "lng"], "利用者情報")
    if not ({"arrive", "depart"} <= set(col) or "plan" in col):
        raise ImportErrorJa("「利用者情報」シートに必要な見出しがありません: 施設着, 施設発（または 利用時間帯）")

    recs = []
    for n, r in enumerate(rows[1:], start=2):
        uid = nz(cell(r, col["id"]))
        if not uid:
            continue
        d = to_date(cell(r, col["date"]))
        try:
            lat, lng = float(nz(cell(r, col["lat"]))), float(nz(cell(r, col["lng"])))
        except ValueError:
            warn(f"利用者情報 {n}行目 {uid}: 緯度・経度がないため除外しました")
            continue
        if d is None:
            warn(f"利用者情報 {n}行目 {uid}: 日付がないため除外しました")
            continue
        care_s = nz(cell(r, col.get("care")))
        if care_s not in CARE_MAP:
            warn(f"利用者情報 {n}行目 {uid}: 介助「{care_s}」は不明なため 1（軽）としました")
        item = nz(cell(r, col.get("item")))
        recs.append({
            "date": d, "user_id": uid, "lat": lat, "lng": lng, "plan_id": nz(cell(r, col.get("plan"))),
            "arrive": to_hm(cell(r, col.get("arrive"))), "depart": to_hm(cell(r, col.get("depart"))),
            "care_need": CARE_MAP.get(care_s, 1), "walker": int("歩行器" in item),
            "wheelchair": int("車椅子" in item), "service_min": to_int(cell(r, col.get("service"))),
            "ng": [x for x in NG_SPLIT.split(nz(cell(r, col.get("ng")))) if x],
            "_item": item,
        })
    if not recs:
        raise ImportErrorJa("「利用者情報」に利用者がいません")

    odd_items = sorted({x["_item"] for x in recs if x["_item"] and not (x["walker"] or x["wheelchair"])})
    if odd_items:
        warn(f"持参品のうち計算に使わないもの: {', '.join(odd_items)}")

    # 利用者ごとに、最も新しい日の値に統一
    by_user: dict[str, list[dict]] = defaultdict(list)
    for x in sorted(recs, key=lambda x: x["date"]):
        by_user[x["user_id"]].append(x)
    users, moved = [], []
    for uid, xs in by_user.items():
        last = xs[-1]
        if len({(x["lat"], x["lng"]) for x in xs}) > 1:
            moved.append(uid)
        users.append({"user_id": uid, "lat": last["lat"], "lng": last["lng"], "care_need": last["care_need"],
                      "walker": last["walker"], "seats": 1,
                      "service_min": "" if last["service_min"] is None else last["service_min"],
                      "wheelchair": last["wheelchair"]})
    if moved:
        warn(f"日によって座標が違う利用者は、最も新しい日の座標に統一しました: {', '.join(moved)}")

    # 利用（日ごと）. 介助・歩行器・車椅子・乗車必要時間はその日の値を持たせる
    att, seen = [], {}
    for x in recs:
        row = {"date": x["date"], "user_id": x["user_id"], "arrive": x["arrive"], "depart": x["depart"],
               "plan_id": x["plan_id"],
               "care_need": x["care_need"], "walker": x["walker"], "wheelchair": x["wheelchair"],
               "service_min": "" if x["service_min"] is None else x["service_min"]}
        key = (x["date"], x["user_id"])
        if key in seen:
            warn(f"{x['date']} の {x['user_id']} が2行あります（後の行を使用）")
            att[seen[key]] = row
            continue
        seen[key] = len(att)
        att.append(row)

    ids = set(by_user)
    ng, unknown = set(), set()
    for x in recs:
        for other in x["ng"]:
            if other == x["user_id"]:
                continue
            if other not in ids:
                unknown.add(other)
            ng.add(tuple(sorted((x["user_id"], other))))
    if unknown:
        warn(f"組み合わせ禁止に、利用者情報にないIDがあります（その日に来れば有効）: {', '.join(sorted(unknown))}")
    ng_rows = [{"user_a": a, "user_b": b} for a, b in sorted(ng)]
    return users, att, ng_rows


# ---------------------------------------------------------------- 全体
def convert(xlsx: str | Path, out: str | Path, facility: tuple[float, float] | None = None,
            log=print) -> list[str]:
    """変換して CSV を書き出す. 戻り値は警告のリスト."""
    warnings: list[str] = []

    def warn(msg: str) -> None:
        warnings.append(msg)
        log(f"  [警告] {msg}")

    wb = load_workbook(xlsx, read_only=True, data_only=True)
    try:
        patterns, dup = read_patterns(wb)
        staff, shifts = read_staff(wb, patterns, dup, warn)
        cars = read_cars(wb, warn)
        plans = read_plans(wb)
        users, att, ng = read_users(wb, warn)
    finally:
        wb.close()

    # 施設着・施設発が空欄の行は、利用時間帯（送迎情報のプラン）の時刻で補う
    plan_t = {p["plan_id"]: (p["arrive"], p["depart"]) for p in plans}
    bad = set()
    for a in att:
        if a["arrive"] and a["depart"]:
            continue
        if a["plan_id"] not in plan_t:
            bad.add(a["plan_id"] or f"{a['date']} {a['user_id']}（施設着・施設発が空欄）")
            continue
        pa, pd_ = plan_t[a["plan_id"]]
        a["arrive"], a["depart"] = a["arrive"] or pa, a["depart"] or pd_
    if bad:
        raise ImportErrorJa(f"施設着・施設発が決められません（利用時間帯が「送迎情報」のIDにない）: {', '.join(sorted(bad))}")
    att_dates = sorted({a["date"] for a in att})
    no_staff = [d for d in att_dates if d not in {s["date"] for s in shifts}]
    if no_staff:
        warn(f"利用者がいるのに出勤職員がいない日: {', '.join(no_staff)}（シフトの日付範囲を確認してください）")

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    def save(rows, cols, name):
        pd.DataFrame(rows, columns=cols).to_csv(out / name, index=False, encoding="utf-8-sig")

    save(staff, ["staff_id", "priority", "care_skill", "employment", "home_lat", "home_lng",
                 "direct_start", "direct_end"], "staff.csv")
    save(shifts, ["date", "staff_id", "start", "end"], "shifts.csv")
    save(cars, ["car_id", "capacity", "walker_slots", "owner_staff_id", "wheelchair_slots", "help"], "cars.csv")
    save(plans, ["plan_id", "arrive", "depart"], "plans.csv")
    save(users, ["user_id", "lat", "lng", "care_need", "walker", "seats", "service_min", "wheelchair"], "users.csv")
    save(att, ["date", "user_id", "arrive", "depart", "plan_id", "care_need", "walker", "wheelchair", "service_min"],
         "attendance.csv")
    save(ng, ["user_a", "user_b"], "ng_pairs.csv")

    fac = out / "facility.csv"
    if facility is not None:
        save([{"lat": facility[0], "lng": facility[1]}], ["lat", "lng"], "facility.csv")
    elif not fac.exists():
        save([], ["lat", "lng"], "facility.csv")
        warn(f"施設の緯度経度が未設定です。{fac} の2行目に「緯度,経度」を書くか、--facility で指定してください")

    log(f"職員 {len(staff)} 名（シフト {len(shifts)} 件） / 車 {len(cars)} 台 / プラン {len(plans)} / "
        f"利用者 {len(users)} 名 / 利用 {len(att)} 件（{', '.join(att_dates)}） / 同乗NG {len(ng)} 組")
    return warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="送迎テンプレート（Excel）→ CSV 変換")
    ap.add_argument("excel", help="送迎テンプレートの Excel ファイル（.xlsm / .xlsx）")
    ap.add_argument("--out", default="data/real", help="出力フォルダ（既定: data/real）")
    ap.add_argument("--facility", default=None, help="施設の緯度,経度（例: 36.25,137.97）")
    a = ap.parse_args(argv)
    fac = None
    if a.facility:
        try:
            lat, lng = (float(x) for x in nz(a.facility).split(","))
            fac = (lat, lng)
        except ValueError:
            print("[ERROR] --facility は「緯度,経度」の形で指定してください（例: 36.25,137.97）")
            return 1
    print(f"変換: {a.excel} → {a.out}")
    try:
        convert(a.excel, a.out, fac)
    except ImportErrorJa as e:
        print(f"[ERROR] {e}")
        return 1
    except FileNotFoundError:
        print(f"[ERROR] ファイルが見つかりません: {a.excel}")
        return 1
    except PermissionError:
        print("[ERROR] ファイルを開けません。Excel で開いている場合は閉じてから実行してください")
        return 1
    print("完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
