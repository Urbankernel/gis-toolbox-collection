"""采样点生成：本地矢量路网 → 沿路等间距采样点（arcpy + shapely，零 geopandas 依赖）。

原版用 geopandas（read_file / estimate_utm_crs / to_crs / interpolate），改造后：
- 读路网 / 投影：arcpy（SearchCursor 读线 + geometry.projectAs 投影到 UTM）；
- 沿路插值 / 方位角：shapely（LineString.interpolate / 纯几何）。

分层：`generate_points_utm` / `_utm_epsg` / `_interpolate_line` 为纯逻辑（无 arcpy，
命令行可测）；`sample_from_fc` 为 arcpy 桥接层（延迟 import arcpy）。
"""

from __future__ import annotations

import math
from typing import Any

from shapely import wkb as _wkb
from shapely import force_2d
from shapely.geometry import Point, LineString, Polygon, MultiPolygon

from .bearing import compute_bearing_for_point


def _utm_epsg(lng: float, lat: float) -> int:
    """根据中心经纬度算 UTM 投影带 EPSG（北半球 326xx / 南半球 327xx）。"""
    zone = int((lng + 180.0) / 6.0) + 1
    zone = max(1, min(zone, 60))
    return (32600 + zone) if lat >= 0 else (32700 + zone)


def _interpolate_line(line: LineString, spacing: float, offset: float) -> list[tuple[float, float]]:
    """沿单条线（米制）等间距插值，返回 [(x, y), ...]。两端各留 offset。"""
    length = line.length
    if length < offset:
        return []
    if length < offset + spacing:
        mid = line.interpolate(length / 2.0)
        return [(mid.x, mid.y)]
    positions = []
    cur = float(offset)
    while cur <= length - offset:
        positions.append(cur)
        cur += spacing
    return [(line.interpolate(d).x, line.interpolate(d).y) for d in positions]


def generate_points_utm(lines: list[tuple[str, LineString]],
                        spacing: float = 40, offset: float = 15) -> list[dict[str, Any]]:
    """沿 UTM 米制路网等间距生成采样点，并计算 canonical bearing。

    lines: list[(street_id, LineString)]，几何必须为米制投影坐标（UTM）。
    返回 list[dict]：point_id / street_id / x / y / bearing（x,y 为 UTM 米制坐标）。
    """
    out: list[dict[str, Any]] = []
    counter = 1
    for street_id, line in lines:
        for (x, y) in _interpolate_line(line, spacing, offset):
            bearing = compute_bearing_for_point(Point(x, y), line)
            out.append({
                "point_id": f"point_{counter}",
                "street_id": street_id,
                "x": x,
                "y": y,
                "bearing": bearing,
            })
            counter += 1
    return out


def _load_area_polygon(area_fc: str) -> Polygon | MultiPolygon | None:
    """读研究区面要素类 → shapely 几何（WGS84，多面取并集）。空则返回 None。"""
    import arcpy

    wgs84 = arcpy.SpatialReference(4326)
    polys: list = []
    with arcpy.da.SearchCursor(area_fc, ["SHAPE@"]) as cur:
        for (g,) in cur:
            g_wgs = g.projectAs(wgs84)
            s = _wkb.loads(bytes(g_wgs.WKB))
            if s.geom_type in ("Polygon", "MultiPolygon"):
                polys.append(s)
    if not polys:
        return None
    merged = polys[0]
    for p in polys[1:]:
        merged = merged.union(p)
    return merged


def sample_from_fc(roads_fc: str, spacing: float = 40, offset: float = 15,
                   area_fc: str | None = None) -> list[dict[str, Any]]:
    """从本地路网要素类生成采样点，返回 list[dict]（WGS84）。

    返回字段：point_id / street_id / bearing / lat / lon（均 WGS84）。
    area_fc 提供时，采样后仅保留研究区内的点。
    """
    import arcpy

    wgs84 = arcpy.SpatialReference(4326)

    # 1. 读路网线，统一 projectAs 到 WGS84（不依赖路网原始 CRS）
    road_geoms_wgs: list[tuple[str, Any]] = []
    with arcpy.da.SearchCursor(roads_fc, ["SHAPE@"]) as cur:
        for i, (g,) in enumerate(cur):
            road_geoms_wgs.append((f"street_{i + 1}", g.projectAs(wgs84)))
    if not road_geoms_wgs:
        raise ValueError("路网要素类为空，无法生成采样点")

    # 2. 用 WGS84 范围中心确定 UTM 投影带
    extents = [g.extent for _, g in road_geoms_wgs]
    xmin = min(e.XMin for e in extents)
    xmax = max(e.XMax for e in extents)
    ymin = min(e.YMin for e in extents)
    ymax = max(e.YMax for e in extents)
    utm_sr = arcpy.SpatialReference(_utm_epsg((xmin + xmax) / 2.0, (ymin + ymax) / 2.0))

    # 3. 投影到 UTM + 转 shapely（MultiLineString 展平为多段）
    lines_utm: list[tuple[str, LineString]] = []
    for street_id, g in road_geoms_wgs:
        # 强制 2D：CAD/DWG 转换的路网常带 Z/M，3D 线会让后续 substring/插值出问题
        s = force_2d(_wkb.loads(bytes(g.projectAs(utm_sr).WKB)))
        if s.geom_type == "MultiLineString":
            parts = list(s.geoms)
            if len(parts) == 1:
                lines_utm.append((street_id, parts[0]))
            else:
                for j, part in enumerate(parts):
                    lines_utm.append((f"{street_id}_{j}", part))
        elif s.geom_type == "LineString":
            lines_utm.append((street_id, s))
        # 非线要素（点/面）跳过

    # 4. 插值采样 + bearing（UTM 米制）
    pts_utm = generate_points_utm(lines_utm, spacing, offset)
    if not pts_utm:
        raise ValueError("未生成任何采样点：路网过短或 spacing/offset 过大")

    # 5. 采样点 UTM → WGS84
    records: list[dict[str, Any]] = []
    for p in pts_utm:
        pg = arcpy.PointGeometry(arcpy.Point(p["x"], p["y"]), utm_sr)
        pg_wgs = pg.projectAs(wgs84)
        records.append({
            "point_id": p["point_id"],
            "street_id": p["street_id"],
            "bearing": round(p["bearing"], 1),
            "lat": round(pg_wgs.firstPoint.Y, 7),
            "lon": round(pg_wgs.firstPoint.X, 7),
        })

    # 6. 研究区点级裁剪（可选）
    if area_fc:
        area_poly = _load_area_polygon(area_fc)
        if area_poly is not None:
            records = [r for r in records
                       if Point(r["lon"], r["lat"]).intersects(area_poly)]

    return records
