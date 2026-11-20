"""成果导出：矢量（geo_common.exporter）+ 栅格（arcpy.NumPyArrayToRaster）。

从原项目 export.py 改造：geopandas → geo_common.exporter，
rasterio → arcpy.NumPyArrayToRaster。坐标系一律 WGS-84。
"""

from __future__ import annotations

import os
from typing import Any

from geo_common.exporter import write_points, write_polygons

from .interpolate import band_label

# 等时带面字段（label 为 "5-10min" / ">30min" 形式）
ISOC_FIELD_DEFS: list[tuple[str, str]] = [
    ("origin_id", "LONG"),
    ("lo", "DOUBLE"),
    ("hi", "DOUBLE"),
    ("label", "TEXT"),
]

# 不可达点字段（网格点序号用 pid，避免与 SHP 默认 Id 字段大小写不敏感冲突）
UNREACH_FIELD_DEFS: list[tuple[str, str]] = [
    ("pid", "LONG"),
    ("origin_id", "LONG"),
]


def write_isochrones(out_path: str, bands: list[dict], origin_id: int,
                     multi_origin: bool = False) -> str:
    """写等时带面（核心成果）。"""
    if not bands:
        return out_path
    features = []
    for b in bands:
        features.append({
            "origin_id": origin_id,
            "lo": b["lo"],
            "hi": b["hi"] if b["hi"] is not None else None,
            "label": f"{band_label(b)}min",
            "geom": b["geometry"],
        })
    layer = f"isochrone_o{origin_id}" if multi_origin else "isochrone"
    return write_polygons(out_path, features, ISOC_FIELD_DEFS, layer_name=layer)


def write_fishnet_points(out_path: str, points: list,
                         per_origin_values: dict[int, list],
                         multi_origin: bool = False) -> str:
    """写渔网终点：宽表 time_o{origin_id} 列（分钟），None 记为不可达。"""
    ids = sorted(per_origin_values)
    field_defs: list[tuple[str, str]] = [("pid", "LONG"), ("lon", "DOUBLE"), ("lat", "DOUBLE")]
    time_fields = [f"time_o{i}" for i in ids]
    for tf in time_fields:
        field_defs.append((tf, "DOUBLE"))

    features = []
    for idx, p in enumerate(points):
        feat: dict[str, Any] = {
            "pid": p.oid, "lon": round(p.lon, 6), "lat": round(p.lat, 6),
        }
        for i in ids:
            v = per_origin_values[i][idx]
            feat[f"time_o{i}"] = v
        feat["geom"] = _point_geom(p.lon, p.lat)
        features.append(feat)
    return write_points(out_path, features, field_defs, layer_name="fishnet")


def write_unreachable(out_path: str, points: list, origin_id: int,
                      multi_origin: bool = False) -> str:
    """写不可达点（排查路网孤岛）。"""
    if not points:
        return out_path
    features = [{
        "pid": p.oid, "origin_id": origin_id, "geom": _point_geom(p.lon, p.lat),
    } for p in points]
    layer = f"unreachable_o{origin_id}" if multi_origin else "unreachable"
    return write_points(out_path, features, UNREACH_FIELD_DEFS, layer_name=layer)


def _point_geom(lon: float, lat: float):
    from shapely.geometry import Point
    return Point(lon, lat)


def write_raster(out_tif: str, masked, gt: tuple, nodata: float = -9999.0) -> str:
    """IDW 时间表面 → GeoTIFF（float32，研究区外 nodata）。

    gt 为 6 元组 geotransform (xmin, dx, 0, ymax, 0, -dy)；
    masked 为 np.ma.MaskedArray，行方向自北向南（data[0] 为最北行）。
    """
    import arcpy
    import numpy as np

    data = masked.filled(nodata).astype("float32")
    nrows, ncols = data.shape
    xmin, dx, _, ymax, _, mdy = gt
    dy = -mdy

    # 左下角：xmin + 最北边 ymax 向下推 nrows 个像元
    lower_left = arcpy.Point(xmin, ymax - nrows * dy)

    arcpy.env.overwriteOutput = True
    os.makedirs(os.path.dirname(os.path.abspath(out_tif)), exist_ok=True)

    sr = arcpy.SpatialReference(4326)  # WGS-84
    raster = arcpy.NumPyArrayToRaster(
        data, lower_left, dx, dy, value_to_nodata=nodata)
    # 补坐标系标签（不重投影，数据本就是 WGS-84）
    arcpy.management.DefineProjection(raster, sr)
    # 导出为 GeoTIFF，显式指定 nodata
    arcpy.management.CopyRaster(raster, out_tif, nodata_value=nodata)
    return out_tif
