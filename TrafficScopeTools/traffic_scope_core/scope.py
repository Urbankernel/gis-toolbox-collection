"""范围面读取：从范围面要素类（或图层对象）→ WGS84 shapely 面。

替代原项目 geopandas 读文件路径（shp/geojson/kml/gpkg）——ArcGIS 工具箱里
范围直接由「范围面要素类」参数喂入（拖图层/浏览选文件均可）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


@dataclass
class ScopeArea:
    polygon: BaseGeometry          # WGS84 Polygon / MultiPolygon
    source: str = "feature_class"
    name: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.polygon.bounds

    def area_km2(self) -> float:
        """范围面面积（平方公里，球面近似）。"""
        minx, miny, maxx, maxy = self.polygon.bounds
        lat = math.radians((miny + maxy) / 2)
        deg2_km2 = (111.32 * math.cos(lat)) * 110.57
        return self.polygon.area * deg2_km2


def normalize_polygon(geom: BaseGeometry | None = None,
                      coords=None) -> BaseGeometry:
    """几何/坐标序列 → 合法 Polygon/MultiPolygon。

    脏数据对策：未闭合自动闭合；自相交用 make_valid 修复；
    修复结果为 GeometryCollection 时只保留面构件。
    """
    if geom is None:
        if coords is None:
            raise ValueError("coords 与 geom 至少提供一个")
        ring = [[float(x), float(y)] for x, y in coords]
        if len(ring) < 3:
            raise ValueError(f"多边形顶点数不足（{len(ring)} < 3）")
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        geom = Polygon(ring)

    if not geom.is_valid:
        fixed = shapely.make_valid(geom)
        if fixed.geom_type == "GeometryCollection":
            polys = [g for g in fixed.geoms
                     if g.geom_type in ("Polygon", "MultiPolygon")]
            if not polys:
                raise ValueError("多边形修复失败：无有效面构件")
            fixed = (polys[0] if len(polys) == 1
                     else MultiPolygon([p for g in polys for p in
                                        (g.geoms if g.geom_type == "MultiPolygon"
                                         else [g])]))
        geom = fixed

    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"范围必须是面要素，当前为 {geom.geom_type}")
    if geom.area <= 0:
        raise ValueError("范围面面积为 0")
    return geom


def load_area_from_feature_class(value, name: str = "") -> ScopeArea:
    """从 GPFeatureLayer 参数值读取范围面（多要素 unary_union，投影到 WGS84）。"""
    from geo_common.params import resolve_feature_class
    from shapely import wkb as _wkb
    from shapely.ops import unary_union

    path = resolve_feature_class(value)
    if not path:
        raise ValueError("未提供有效的范围面要素类")

    import arcpy
    sr = arcpy.Describe(path).spatialReference
    wgs84 = arcpy.SpatialReference(4326)
    need_project = sr is not None and sr.factoryCode not in (4326, 0)

    polys = []
    with arcpy.da.SearchCursor(path, ["SHAPE@"]) as cur:
        for row in cur:
            geom = row[0]
            if geom is None:
                continue
            if need_project:
                geom = geom.projectAs(wgs84)
            polys.append(_wkb.loads(bytes(geom.WKB)))

    if not polys:
        raise ValueError("范围面要素类为空（无面要素）")

    polygon = unary_union(polys)
    return ScopeArea(polygon=normalize_polygon(geom=polygon),
                     name=name or path)
