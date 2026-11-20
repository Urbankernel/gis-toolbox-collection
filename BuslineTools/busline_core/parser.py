# -*- coding: utf-8 -*-
"""高德公交线路 / 站点 输出字段定义、响应解析与去重。

- LINE_OUT_FIELDS / STOP_OUT_FIELDS：矢量要素类属性字段（全部 TEXT，编码/坐标串一律文本）。
- LINE_SHP_RENAME / STOP_SHP_RENAME：SHP 字段名 ≤10 字符映射（GPKG 保留原名）。
- amap_parse：单条 busline API 响应 → (线路记录, 站点记录列表)。
- 去重：线路按规范化 route_name，站点按 (route_name, station_index, station_name) 复合键。
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
from typing import Any

# 线路属性字段（21 个 TEXT；轨迹坐标在 polyline/polyline_wgs84 里，几何单独写入）
LINE_OUT_FIELDS: list[tuple[str, str]] = [
    ("line_name", "TEXT"),        # 线路短名（去掉括号内容）
    ("route_id", "TEXT"),         # 线路 ID
    ("route_name", "TEXT"),       # 线路完整名（含起终点方向）
    ("type", "TEXT"),             # 线路类型
    ("polyline", "TEXT"),         # 轨迹坐标串（GCJ-02 原始）
    ("polyline_wgs84", "TEXT"),   # 轨迹坐标串（WGS84）
    ("citycode", "TEXT"),
    ("start_stop", "TEXT"),
    ("end_stop", "TEXT"),
    ("start_time", "TEXT"),
    ("end_time", "TEXT"),
    ("uicolor", "TEXT"),
    ("timedesc", "TEXT"),         # 发车时刻表（解码后 JSON）
    ("loop", "TEXT"),
    ("status", "TEXT"),
    ("direc", "TEXT"),
    ("company", "TEXT"),
    ("distance", "TEXT"),
    ("basic_price", "TEXT"),
    ("total_price", "TEXT"),
    ("bounds", "TEXT"),
]

# 站点属性字段（11 个 TEXT；lng/lat 为 GCJ-02 原始，lng_wgs84/lat_wgs84 为 WGS84）
STOP_OUT_FIELDS: list[tuple[str, str]] = [
    ("line_name", "TEXT"),
    ("route_id", "TEXT"),
    ("route_name", "TEXT"),
    ("station_id", "TEXT"),
    ("station_name", "TEXT"),
    ("station_index", "TEXT"),
    ("location", "TEXT"),
    ("lng", "TEXT"),
    ("lat", "TEXT"),
    ("lng_wgs84", "TEXT"),
    ("lat_wgs84", "TEXT"),
]

# SHP 超长字段名映射（GPKG 保留原名）
LINE_SHP_RENAME: dict[str, str] = {
    "polyline_wgs84": "line_wgs84",
    "basic_price": "base_price",
    "total_price": "total_pric",
}

STOP_SHP_RENAME: dict[str, str] = {
    "station_name": "stn_name",
    "station_index": "stn_index",
}


def _as_text(value: Any) -> str:
    """字段安全转文本：None→空串，dict→取 name，list/tuple→逗号拼接，其余→str。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _as_text(value.get("name") or value.get("id") or "")
    if isinstance(value, (list, tuple)):
        return ",".join(_as_text(v) for v in value if v not in (None, ""))
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def short_name(name: Any) -> str:
    """线路短名：取完整名里第一个 '(' 之前的部分（如 "1路(火车站-汽车站)" → "1路"）。"""
    n = _as_text(name)
    idx = n.find("(")
    return n[:idx].strip() if idx >= 0 else n.strip()


def decode_timedesc(raw: Any) -> str:
    """解码 timedesc（URL 编码的 JSON 字符串）为可读紧凑 JSON；失败原样返回。"""
    raw = _as_text(raw)
    if not raw:
        return ""
    try:
        obj = json.loads(urllib.parse.unquote(raw))
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except (ValueError, json.JSONDecodeError):
        return raw


def amap_parse(busline: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """解析单条高德 busline 响应 → (线路记录, 站点记录列表)。

    线路记录含 extra 键 `coord_arr`（GCJ-02 [(lng, lat), ...]，供 writer 造几何）。
    无 id 或无坐标时线路返回 None。
    """
    route_id = _as_text(busline.get("id"))
    if not route_id:
        return None, []

    route_name = _as_text(busline.get("name"))
    polyline = _as_text(busline.get("polyline"))

    # 轨迹坐标数组（GCJ-02 原始）
    coord_arr: list[tuple[float, float]] = []
    if polyline:
        for pt in polyline.split(";"):
            pt = pt.strip()
            if not pt:
                continue
            parts = pt.split(",")
            if len(parts) != 2:
                continue
            try:
                coord_arr.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue

    line_rec: dict[str, Any] = {
        "line_name": short_name(route_name),
        "route_id": route_id,
        "route_name": route_name,
        "type": _as_text(busline.get("type")),
        "polyline": polyline,
        "polyline_wgs84": "",
        "citycode": _as_text(busline.get("citycode")),
        "start_stop": _as_text(busline.get("start_stop")),
        "end_stop": _as_text(busline.get("end_stop")),
        "start_time": _as_text(busline.get("start_time")),
        "end_time": _as_text(busline.get("end_time")),
        "uicolor": _as_text(busline.get("uicolor")),
        "timedesc": decode_timedesc(busline.get("timedesc")),
        "loop": _as_text(busline.get("loop")),
        "status": _as_text(busline.get("status")),
        "direc": _as_text(busline.get("direc")),
        "company": _as_text(busline.get("company")),
        "distance": _as_text(busline.get("distance")),
        "basic_price": _as_text(busline.get("basic_price")),
        "total_price": _as_text(busline.get("total_price")),
        "bounds": _as_text(busline.get("bounds")),
        "coord_arr": coord_arr,
    }

    # 站点：busstops（兼容 via_stops）
    stops = busline.get("busstops") or busline.get("via_stops") or []
    stop_recs: list[dict[str, Any]] = []
    for st in stops:
        if not isinstance(st, dict):
            continue
        loc = _as_text(st.get("location"))
        lng = lat = ""
        if loc:
            parts = loc.split(",")
            if len(parts) == 2:
                lng, lat = parts[0].strip(), parts[1].strip()
        stop_recs.append({
            "line_name": short_name(route_name),
            "route_id": route_id,
            "route_name": route_name,
            "station_id": _as_text(st.get("id")),
            "station_name": _as_text(st.get("name")),
            "station_index": _as_text(st.get("sequence")),
            "location": loc,
            "lng": lng,
            "lat": lat,
            "lng_wgs84": "",
            "lat_wgs84": "",
        })

    return line_rec, stop_recs


def norm_line_key(name: Any) -> str:
    """线路去重键规范化：NFKC 全角→半角、去空白、英文小写。"""
    n = unicodedata.normalize("NFKC", _as_text(name))
    n = re.sub(r"\s+", "", n)
    return n.casefold()


def dedup_lines(line_rows: list[dict[str, Any]],
                stop_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """输出前兜底去重：线路按规范化 route_name，站点按复合键。返回 (去重线路, 去重站点)。"""
    seen_line: set[str] = set()
    kept_lines: list[dict[str, Any]] = []
    for ln in line_rows:
        k = norm_line_key(ln.get("route_name", ""))
        if k in seen_line:
            continue
        seen_line.add(k)
        kept_lines.append(ln)

    seen_stop: set[tuple[str, str, str]] = set()
    kept_stops: list[dict[str, Any]] = []
    for st in stop_rows:
        sk = (norm_line_key(st.get("route_name", "")),
              str(st.get("station_index", "")).strip(),
              norm_line_key(st.get("station_name", "")))
        if sk in seen_stop:
            continue
        seen_stop.add(sk)
        kept_stops.append(st)

    return kept_lines, kept_stops
