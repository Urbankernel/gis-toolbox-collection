"""范围界定与出发点读取：从 arcpy 要素类读几何 → shapely。

从原项目 study_area/base.py 移植 normalize_polygon，读几何用 arcpy SearchCursor
（替代 geopandas read_file）。arcpy 延迟导入，顶层零依赖。
"""

from __future__ import annotations

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


def close_ring(coords: list) -> list[list[float]]:
    """闭合环（容忍外部数据未闭合的脏数据）。"""
    ring = [[float(x), float(y)] for x, y in coords]
    if len(ring) < 3:
        raise ValueError(f"多边形顶点数不足（{len(ring)} < 3）")
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def normalize_polygon(coords=None, geom: BaseGeometry | None = None) -> BaseGeometry:
    """坐标序列或几何 → 合法 Polygon/MultiPolygon。

    脏数据对策：未闭合自动闭合；自相交 make_valid 修复；
    GeometryCollection 只保留面构件。
    """
    if geom is None:
        if coords is None:
            raise ValueError("coords 与 geom 至少提供一个")
        geom = Polygon(close_ring(coords))

    if not geom.is_valid:
        fixed = shapely.make_valid(geom)
        if fixed.geom_type == "GeometryCollection":
            polys = [g for g in fixed.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
            if not polys:
                raise ValueError("多边形修复失败：无有效面构件")
            fixed = polys[0] if len(polys) == 1 else MultiPolygon(
                [p for g in polys for p in (g.geoms if g.geom_type == "MultiPolygon" else [g])])
        geom = fixed

    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"范围必须是面要素，当前为 {geom.geom_type}")
    if geom.area <= 0:
        raise ValueError("范围面面积为 0")
    return geom


def _load_geom(arcpy_geom) -> BaseGeometry:
    """arcpy 几何 → shapely 几何（WKB 桥）。"""
    from shapely import wkb as _wkb
    return _wkb.loads(bytes(arcpy_geom.WKB))


def read_polygon(fc_path: str) -> BaseGeometry:
    """读面要素类 → shapely Polygon/MultiPolygon（多个面取 union 合并为一个研究区）。"""
    import arcpy
    geoms = []
    with arcpy.da.SearchCursor(fc_path, ["SHAPE@"]) as cur:
        for (g,) in cur:
            geoms.append(_load_geom(g))
    if not geoms:
        raise ValueError("研究区面要素类为空")
    merged = shapely.union_all(geoms) if len(geoms) > 1 else geoms[0]
    return normalize_polygon(geom=merged)


def read_origins(fc_path: str) -> list[tuple[float, float]]:
    """读点要素类 → [(lon, lat), ...]（WGS-84，仅取 XY）。"""
    import arcpy
    origins = []
    with arcpy.da.SearchCursor(fc_path, ["SHAPE@"]) as cur:
        for (g,) in cur:
            s = _load_geom(g)
            origins.append((float(s.x), float(s.y)))
    if not origins:
        raise ValueError("出发点要素类为空")
    return origins
