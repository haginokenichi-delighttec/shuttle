"""送迎表ビューアで氏名を表示するための名簿を書き出す（手元のPC専用）.

    python -m shuttle_opt.routing.export_names 送迎テンプレート.xlsm --out data/real

出力: <out>/names.csv（kind,id,name）
    kind=staff … シフトのシート（「月次シフト」または「○月_シフト」）の職員ID と、その左の列（B列）の氏名
    kind=user  … 「利用者情報」の利用者ID と「名前」列

※ 氏名を含むので、このファイルはクラウドやメールで外部に出さないこと。
   ソルバー（main.py）はこのファイルを読みません。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from .excel_import import ImportErrorJa, find_cols, nz, sheet_rows, shift_sheet_name


def read_names(xlsx: str | Path) -> list[dict]:
    wb = load_workbook(xlsx, read_only=True, data_only=True)
    try:
        sheet = shift_sheet_name(wb)
        rows = sheet_rows(wb, sheet)
        hdr_i = next((i for i, r in enumerate(rows[:20]) if "職員ID" in [nz(v) for v in r]), None)
        if hdr_i is None:
            raise ImportErrorJa(f"「{sheet}」シートに「職員ID」の見出しが見つかりません")
        id_col = [nz(v) for v in rows[hdr_i]].index("職員ID")
        name_col = id_col - 1                     # 氏名は職員IDの左の列（見出しなし）
        out, seen = [], set()
        for r in rows[hdr_i + 1:]:
            sid = nz(r[id_col]) if id_col < len(r) else ""
            if sid and ("staff", sid) not in seen:
                seen.add(("staff", sid))
                out.append({"kind": "staff", "id": sid, "name": nz(r[name_col]) if name_col >= 0 else ""})

        rows = sheet_rows(wb, "利用者情報")
        col = find_cols(rows[0], {"id": ["利用者ID"], "name": ["名前", "氏名"]}, ["id", "name"], "利用者情報")
        for r in rows[1:]:
            uid = nz(r[col["id"]]) if col["id"] < len(r) else ""
            name = nz(r[col["name"]]) if col["name"] < len(r) else ""
            if uid and name and ("user", uid) not in seen:
                seen.add(("user", uid))
                out.append({"kind": "user", "id": uid, "name": name})
    finally:
        wb.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="送迎表ビューア用の氏名名簿を書き出す")
    ap.add_argument("excel", help="送迎テンプレートの Excel ファイル（.xlsm / .xlsx）")
    ap.add_argument("--out", default="data/real", help="出力フォルダ（既定: data/real）")
    a = ap.parse_args(argv)
    try:
        rows = read_names(a.excel)
    except ImportErrorJa as e:
        print(f"[ERROR] {e}")
        return 1
    except FileNotFoundError:
        print(f"[ERROR] ファイルが見つかりません: {a.excel}")
        return 1
    except PermissionError:
        print("[ERROR] ファイルを開けません。Excel で開いている場合は閉じてから実行してください")
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["kind", "id", "name"]).to_csv(out / "names.csv", index=False, encoding="utf-8-sig")
    n_s = sum(r["kind"] == "staff" for r in rows)
    print(f"名簿を書き出しました: {out / 'names.csv'}（職員 {n_s} 名 / 利用者 {len(rows) - n_s} 名）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
