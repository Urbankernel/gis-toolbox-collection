# -*- coding: utf-8 -*-
"""arcpy 写 AOI 面要素类：AOIRecord（GeoJSON 几何）→ shapely → geo_common 面图层导出。

这是 get_aoi_core 里唯一的 arcpy/shapely 依赖模块，替代原项目
exporters/geojson·shapefile·geopackage·csv 那一层，只保留矢量输出（SHP/GPKG）。
"""
import json
from typing import Any, Sequence

from geo_common.exporter import write_polygons
from shapely.geometry import shape as shapely_shape

from .models import AOIRecord

# AOI 面属性字段：[(字段名, 类型)]。编码字段（typecode/adcode/citycode 等）一律 TEXT，
# 防前导 0 丢失；tag 数组字段存 JSON 字符串。
AOI_FIELD_DEFS: list[tuple[str, str]] = [
    ("poi_id", "TEXT"),
    ("name", "TEXT"),
    ("input_name", "TEXT"),
    ("provider", "TEXT"),
    ("poi_type", "TEXT"),
    ("address", "TEXT"),
    ("area_km2", "DOUBLE"),
    ("status", "TEXT"),
    ("typecode", "TEXT"),
    ("type", "TEXT"),
    ("newtype", "TEXT"),
    ("adcode", "TEXT"),
    ("citycode", "TEXT"),
    ("cityname", "TEXT"),
    ("districtname", "TEXT"),
    ("tel", "TEXT"),
    ("tag_show_info", "TEXT"),
    ("t_tag_list", "TEXT"),
    ("t_tag", "TEXT"),
    ("tag_display", "TEXT"),
]

# SHP 字段名 ≤10 字符映射（GPKG 保留原名）
AOI_SHP_RENAME: dict[str, str] = {
    "districtname": "district",
    "tag_show_info": "tag_show",
    "tag_display": "tag_disp",
}


def _to_text(v: Any) -> str:
    """属性值转文本：None→空串，数组/对象→JSON 字符串，其余→str。"""
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def write_aoi_records(out_path: str, records: Sequence[AOIRecord],
                      spatial_ref: Any = None) -> str:
    """把 AOIRecord 列表写成 AOI 面要素类（SHP / GPKG）。

    返回写入的要素类路径。spatial_ref 非 WGS-84 时由 write_polygons 内部投影。
    """
    features = []
    for rec in records:
        if not rec.geometry:
            continue
        extra = rec.extra or {}
        features.append({
            "poi_id": rec.poi_id,
            "name": rec.name,
            "input_name": rec.input_name,
            "provider": rec.provider,
            "poi_type": rec.poi_type,
            "address": rec.address,
            "area_km2": round(rec.area_km2, 6),
            "status": rec.status.value if hasattr(rec.status, "value") else str(rec.status),
            "typecode": _to_text(extra.get("typecode")),
            "type": _to_text(extra.get("type")),
            "newtype": _to_text(extra.get("newtype")),
            "adcode": _to_text(extra.get("adcode")),
            "citycode": _to_text(extra.get("citycode")),
            "cityname": _to_text(extra.get("cityname")),
            "districtname": _to_text(extra.get("districtname")),
            "tel": _to_text(extra.get("tel")),
            "tag_show_info": _to_text(extra.get("tag_show_info")),
            "t_tag_list": _to_text(extra.get("t_tag_list")),
            "t_tag": _to_text(extra.get("t_tag")),
            "tag_display": _to_text(extra.get("tag_display")),
            "geom": shapely_shape(rec.geometry),
        })
    return write_polygons(out_path, features, AOI_FIELD_DEFS,
                          spatial_ref=spatial_ref, layer_name="aoi",
                          rename_map=AOI_SHP_RENAME)
