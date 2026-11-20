"""渔网终点生成：numpy 规则点阵 + shapely 范围裁剪。

从原项目 grid.py 移植，只依赖 numpy + shapely（零 arcpy / 零 geopandas）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from .coords import haversine_km, meters_to_degrees


@dataclass
class GridPoint:
    oid: int
    lon: float
    lat: float


def make_fishnet(
    polygon: BaseGeometry,
    cell_size: float,
    offset: float = 100.0,
    origins: list[tuple[float, float]] | None = None,
    adaptive_beyond_km: float | None = None,
    adaptive_multiplier: int = 2,
) -> list[GridPoint]:
    """在研究范围内生成渔网格心点。

    - `cell_size` 与 `offset` 单位均为**米**（按研究区中心纬度换算经纬度步长）；
    - 渔网范围在研究区外接矩形基础上外扩 offset，保证边界覆盖；
    - 自适应放疏：距最近出发点超过 adaptive_beyond_km 的点，按 multiplier 抽稀。
    """
    if cell_size <= 0:
        raise ValueError("cell_size 必须 > 0")

    minx, miny, maxx, maxy = polygon.bounds
    lon_c, lat_c = (minx + maxx) / 2, (miny + maxy) / 2

    ox, oy = meters_to_degrees(lon_c, lat_c, offset)
    minx -= ox
    miny -= oy
    maxx += ox
    maxy += oy

    dx, dy = meters_to_degrees(lon_c, lat_c, cell_size)
    xs = np.arange(minx + dx / 2, maxx, dx)
    ys = np.arange(miny + dy / 2, maxy, dy)
    if len(xs) == 0 or len(ys) == 0:
        return []

    gx, gy = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(polygon, gx, gy)

    points: list[GridPoint] = []
    oid = 1
    for iy in range(len(ys)):
        for ix in range(len(xs)):
            if not inside[iy, ix]:
                continue
            lon, lat = float(xs[ix]), float(ys[iy])
            if (
                adaptive_beyond_km is not None
                and origins
                and adaptive_multiplier > 1
            ):
                d = min(haversine_km(olon, olat, lon, lat) for olon, olat in origins)
                if d > adaptive_beyond_km and (ix % adaptive_multiplier or iy % adaptive_multiplier):
                    continue
            points.append(GridPoint(oid=oid, lon=lon, lat=lat))
            oid += 1
    return points
