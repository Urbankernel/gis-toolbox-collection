# -*- coding: utf-8 -*-
"""arcpy 落盘：线路 LineString 图层 + 站点 Point 图层。

busline_core 里唯一的 arcpy / shapely 依赖模块。
线图层用 geo_common.exporter.write_lines，点图层用 write_points。
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from geo_common.exporter import write_lines, write_points

from .parser import (LINE_OUT_FIELDS, LINE_SHP_RENAME, STOP_OUT_FIELDS,
                     STOP_SHP_RENAME)


def _field_names(field_defs: Sequence[tuple[str, str]]) -> list[str]:
    return [n for n, _ in field_defs]


def write_busline(line_path: str, stop_path: str,
                  line_rows: Sequence[dict[str, Any]],
                  stop_rows: Sequence[dict[str, Any]],
                  *, spatial_ref: Any = None) -> tuple[str, str]:
    """写线路（POLYLINE）+ 站点（POINT）两个要素类，返回 (线路路径, 站点路径)。"""
    from shapely.geometry import LineString, Point

    # ---- 线路 ----
    line_names = _field_names(LINE_OUT_FIELDS)
    line_feats: list[dict[str, Any]] = []
    for rec in line_rows:
        coords = rec.get("coord_arr_wgs84") or rec.get("coord_arr") or []
        if len(coords) < 2:
            continue
        feat = {f: rec.get(f, "") for f in line_names}
        feat["geom"] = LineString(coords)
        line_feats.append(feat)
    write_lines(line_path, line_feats, LINE_OUT_FIELDS,
                spatial_ref=spatial_ref, layer_name="busline",
                rename_map=LINE_SHP_RENAME)

    # ---- 站点 ----
    stop_names = _field_names(STOP_OUT_FIELDS)
    stop_feats: list[dict[str, Any]] = []
    for rec in stop_rows:
        lng = rec.get("lng_wgs84") or rec.get("lng")
        lat = rec.get("lat_wgs84") or rec.get("lat")
        if not lng or not lat:
            continue
        try:
            geom = Point(float(lng), float(lat))
        except (ValueError, TypeError):
            continue
        feat = {f: rec.get(f, "") for f in stop_names}
        feat["geom"] = geom
        stop_feats.append(feat)
    write_points(stop_path, stop_feats, STOP_OUT_FIELDS,
                 spatial_ref=spatial_ref, layer_name="busstop",
                 rename_map=STOP_SHP_RENAME)

    return line_path, stop_path
