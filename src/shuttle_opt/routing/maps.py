"""Google マップでルートを開く URL（Maps URLs. API キー不要）.

    https://www.google.com/maps/dir/?api=1
        &waypoints=利用者宅1の緯度,経度|利用者宅2|...
        &destination=施設の緯度,経度
        &travelmode=driving&dir_action=navigate

- ナビ用: origin（出発地）は指定しない → 現在地（施設の駐車場）から案内が始まる。
  origin を付けるとナビではなく経路確認画面になる仕様のため。
- 確認用: origin＝出発地（施設、直行なら職員の自宅）を付け、dir_action は付けない。
- 直帰のルートは destination が職員の自宅になる。
- 立ち寄り先はソルバーが決めた訪問順のまま並べる。
- Google の仕様上、立ち寄り先の数には上限がある（ブラウザでは最大9か所、モバイルブラウザではさらに少ない）。

注意: URL には利用者宅の座標が入るため、出力ファイルは個人情報として扱うこと。
"""
from __future__ import annotations

from urllib.parse import urlencode

BASE = "https://www.google.com/maps/dir/"


def _ll(lat: float, lng: float) -> str:
    return f"{lat:.6f},{lng:.6f}"


def route_url(stops: list[tuple[float, float]], destination: tuple[float, float],
              preview: bool = False, origin: tuple[float, float] | None = None) -> str:
    """stops: 訪問順の (緯度, 経度). destination: 到着地（施設、直帰なら職員の自宅）.

    preview=False: ナビ用。出発地なし → 開いた端末の現在地からナビが始まる
    preview=True : 確認用。出発地＝origin（省略時は destination と同じ＝施設）
                   → ナビは始まらず、経路全体を表示する
    """
    params = {"api": "1"}
    if preview:
        params["origin"] = _ll(*(origin or destination))
    if stops:
        params["waypoints"] = "|".join(_ll(*p) for p in stops)
    params["destination"] = _ll(*destination)
    params["travelmode"] = "driving"
    if not preview:
        params["dir_action"] = "navigate"
    return BASE + "?" + urlencode(params, safe=",")
