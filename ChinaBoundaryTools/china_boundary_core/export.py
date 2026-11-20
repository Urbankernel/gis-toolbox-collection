"""成果导出：边界面要素类（SHP）+ 可选 GeoJSON。

- GeoJSON：零依赖 `json`，原样保留（字段值为 None 的键不写出）。
- 面要素类：用 `geo_common.exporter.write_polygons` 替代原项目的 geopandas
  （arcgispro-py3 缺 geopandas）。桥接：normalize 输出的 GeoJSON dict geometry
  → `shapely.geometry.shape()` 转回 shapely → 写入。

坐标系纪律：源数据即 WGS-84，直接原样输出（EPSG:4326）。
"""
from __future__ import annotations

import json
import logging
import os

from .normalize import OUTPUT_FIELDS

logger = logging.getLogger("chinaboundary.export")

# 边界要素字段（名称均 ≤10 字符，SHP 无需 rename_map）
BOUNDARY_FIELD_DEFS: list[tuple[str, str]] = [
    ("adcode", "TEXT"),
    ("name", "TEXT"),
    ("level", "TEXT"),
    ("parent", "TEXT"),
    ("children", "LONG"),
    ("cen_lng", "DOUBLE"),
    ("cen_lat", "DOUBLE"),
]


def _feature_to_geojson(feature: dict) -> dict:
    props = {k: feature[k] for k in OUTPUT_FIELDS if feature.get(k) is not None}
    return {"type": "Feature", "properties": props, "geometry": feature["geometry"]}


def export_geojson(features: list[dict], path: str) -> str:
    fc = {"type": "FeatureCollection", "features": [_feature_to_geojson(f) for f in features]}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)
    logger.info("已导出 GeoJSON: %s（%d 要素）", path, len(features))
    return path


def export_vector(features: list[dict], out_path: str) -> str:
    """边界面要素类（.shp）→ geo_common.exporter.write_polygons。"""
    from geo_common.exporter import write_polygons
    from shapely.geometry import shape  # 延迟导入，避免 Pro 进程 shapely 循环导入

    rows = []
    for f in features:
        row = {k: f.get(k) for k in OUTPUT_FIELDS}
        row["geom"] = shape(f["geometry"])   # GeoJSON dict → shapely（WGS-84）
        rows.append(row)

    fc = write_polygons(out_path, rows, BOUNDARY_FIELD_DEFS, layer_name="boundary")
    logger.info("已导出面要素类: %s（%d 要素）", fc, len(rows))
    return fc
