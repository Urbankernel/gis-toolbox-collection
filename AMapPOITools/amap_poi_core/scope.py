"""范围输入四模式：面要素 / 行政区 / 缓冲区 / 矩形 → 统一 WGS-84 几何。

坐标系纪律：外部输入可能是投影坐标系（面要素）或 GCJ-02（高德边界 / 显式声明），
进入系统内部一律归一 WGS-84。
"""

from __future__ import annotations

import math
from typing import Any

from shapely import wkt as _wkt
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geo_common.coords import gcj02_to_wgs84

from .client import AmapClient

_KM_PER_DEG_LAT = 110.94
_KM_PER_DEG_LON = 111.32

# 范围模式常量（与 .pyt 参数面板 valueList 一致）
SCOPE_FEATURE = "面要素"
SCOPE_ADMIN = "行政区"
SCOPE_BUFFER = "缓冲区"
SCOPE_BBOX = "矩形范围"


def _fix(geom: BaseGeometry) -> BaseGeometry:
    """修复自相交 / 无效几何。"""
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


# ------------------------------------------------------------------ 面要素

def from_feature_class(fc: str) -> BaseGeometry:
    """读面要素类，任意 CRS 投影到 WGS-84，多面取并集。"""
    import arcpy  # 延迟导入
    sr_wgs84 = arcpy.SpatialReference(4326)
    geoms: list[BaseGeometry] = []
    with arcpy.da.SearchCursor(fc, ["SHAPE@"]) as cur:
        for (g,) in cur:
            if g is None:
                continue
            projected = g.projectAs(sr_wgs84)
            geoms.append(_wkt.loads(projected.WKT))
    if not geoms:
        raise ValueError(f"范围要素类 {fc} 中没有任何要素")
    return _fix(unary_union(geoms))


# ------------------------------------------------------------------ 行政区

def polyline_to_geometry(polyline: str) -> Polygon | MultiPolygon:
    """高德 district polyline（GCJ-02）→ WGS-84 Polygon/MultiPolygon。

    格式：多地块用 "|" 分隔；地块内坐标对用 ";" 分隔；坐标对为 "lng,lat"。
    """
    polygons: list[Polygon] = []
    for block in polyline.split("|"):
        coords: list[tuple[float, float]] = []
        for pair in block.split(";"):
            pair = pair.strip()
            if not pair:
                continue
            try:
                lng, lat = (float(v) for v in pair.split(","))
            except ValueError:
                continue
            coords.append(gcj02_to_wgs84(lng, lat))
        if len(coords) >= 3:
            poly = Polygon(coords)
            poly = poly.buffer(0) if not poly.is_valid else poly
            if not poly.is_empty:
                polygons.append(poly)
    if not polygons:
        raise ValueError("未能从 polyline 解析出有效边界")
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


# ------------------------------------------------------------------ 缓冲区

def circle_polygon(center: list[float], radius_km: float, vertices: int = 64) -> Polygon:
    """生成近似圆多边形（WGS-84）。"""
    lon, lat = float(center[0]), float(center[1])
    dx = radius_km / (_KM_PER_DEG_LON * math.cos(math.radians(lat)))
    dy = radius_km / _KM_PER_DEG_LAT
    points = [
        (lon + dx * math.cos(2 * math.pi * i / vertices),
         lat + dy * math.sin(2 * math.pi * i / vertices))
        for i in range(vertices)
    ]
    return Polygon(points)


def _parse_lonlat(text: str, coord_type: str) -> list[float]:
    """解析 "lon,lat" 字符串，coord_type 为 gcj02 时转 WGS-84。"""
    try:
        lon, lat = (float(v) for v in text.replace("，", ",").split(",", 1))
    except ValueError:
        raise ValueError(f"坐标格式应为 '经度,纬度'，当前: {text!r}")
    if coord_type == "gcj02":
        lon, lat = gcj02_to_wgs84(lon, lat)
    elif coord_type != "wgs84":
        raise ValueError(f"未知 coord_type: {coord_type!r}（支持 wgs84/gcj02）")
    return [lon, lat]


# ------------------------------------------------------------------ 入口

def load_scope(
    scope_type: str,
    client: AmapClient | None = None,
    **params: Any,
) -> tuple[BaseGeometry, str]:
    """按范围模式分发，返回 (WGS-84 几何, 范围名称)。"""
    if scope_type == SCOPE_FEATURE:
        fc = params.get("in_features")
        if not fc:
            raise ValueError("面要素模式需要 in_features")
        return from_feature_class(fc), "范围要素"

    if scope_type == SCOPE_ADMIN:
        keyword = str(params.get("admin_keyword") or "").strip()
        if not keyword:
            raise ValueError("行政区模式需要 adcode 或行政区名称")
        if client is None:
            raise ValueError("行政区模式需要高德客户端（拉取行政区边界）")
        info = client.district(keyword)
        return polyline_to_geometry(info["polyline"]), info["name"]

    if scope_type == SCOPE_BUFFER:
        center_text = str(params.get("center_point") or "").strip()
        radius = float(params.get("radius_km") or 0)
        if not center_text:
            raise ValueError("缓冲区模式需要中心点")
        if radius <= 0:
            raise ValueError("缓冲区模式需要正的 radius_km")
        coord_type = str(params.get("center_coord_type") or "wgs84").lower()
        center = _parse_lonlat(center_text, coord_type)
        return circle_polygon(center, radius), f"buffer_{radius}km"

    if scope_type == SCOPE_BBOX:
        bounds = str(params.get("bbox") or "").strip()
        parts = [v for v in bounds.replace("，", ",").split(",") if v.strip()]
        if len(parts) != 4:
            raise ValueError("矩形范围模式需要 bbox: xmin,ymin,xmax,ymax")
        xmin, ymin, xmax, ymax = (float(v) for v in parts)
        if not (xmin < xmax and ymin < ymax):
            raise ValueError(f"bbox 坐标不合法（要求 xmin<xmax 且 ymin<ymax）: {bounds}")
        coord_type = str(params.get("center_coord_type") or "wgs84").lower()
        if coord_type == "gcj02":
            xmin, ymin = gcj02_to_wgs84(xmin, ymin)
            xmax, ymax = gcj02_to_wgs84(xmax, ymax)
        elif coord_type != "wgs84":
            raise ValueError(f"未知 coord_type: {coord_type!r}（支持 wgs84/gcj02）")
        return box(xmin, ymin, xmax, ymax), "bbox"

    raise ValueError(f"未知范围模式: {scope_type!r}")
