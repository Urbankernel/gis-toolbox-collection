"""高德 POI 输出字段定义与响应解析。

- FIELD_DEFS：矢量要素类的属性字段（35 个，排除 lon/lat 两列坐标，它们进 geometry）。
  注意 poi_type_code / adcode / pcode / citycode 等编码字段一律 TEXT——
  避免前导 0 被当数字丢弃（如 050301 → 50301）。
- SHP_RENAME_MAP：SHP 字段名 ≤10 字符的 6 个超长字段映射（GPKG 保留原名）。
- poi_to_row：单条 API 响应 → 输出行 dict（含 lon/lat 坐标 + 35 个属性）。
"""

from __future__ import annotations

import json
from typing import Any

from geo_common.coords import gcj02_to_wgs84

# 属性字段定义 [(name, type)]，type ∈ TEXT / DOUBLE
# 顺序即要素类字段顺序；lon/lat 不进属性表，作为几何坐标单独写入。
FIELD_DEFS: list[tuple[str, str]] = [
    ("poi_id", "TEXT"),
    ("lon_gcj02", "DOUBLE"),
    ("lat_gcj02", "DOUBLE"),
    ("name", "TEXT"),
    ("poi_type", "TEXT"),
    ("poi_type_code", "TEXT"),
    ("cityname", "TEXT"),
    ("adname", "TEXT"),
    ("address", "TEXT"),
    ("pname", "TEXT"),
    ("pcode", "TEXT"),
    ("citycode", "TEXT"),
    ("adcode", "TEXT"),
    ("tel", "TEXT"),
    ("alias", "TEXT"),
    ("biz_type", "TEXT"),
    ("parent", "TEXT"),
    ("navi_poiid", "TEXT"),
    ("business_area", "TEXT"),
    ("tag", "TEXT"),
    ("biz_rating", "TEXT"),
    ("biz_cost", "TEXT"),
    ("photo_title", "TEXT"),
    ("photo_url", "TEXT"),
    ("cpid", "TEXT"),
    ("floor", "TEXT"),
    ("truefloor", "TEXT"),
    ("parking_type", "TEXT"),
    ("entr_location", "TEXT"),
    ("website", "TEXT"),
    ("email", "TEXT"),
    ("postcode", "TEXT"),
    ("indoor_map", "TEXT"),
    ("indoor_data", "TEXT"),
    ("grid_id", "TEXT"),
]

# SHP 字段名 ≤10 字符映射（与 GPKG/原项目 CSV 保持一致语义）
SHP_RENAME_MAP: dict[str, str] = {
    "poi_type_code": "poi_code",
    "business_area": "biz_area",
    "photo_title": "photo_titl",
    "parking_type": "park_type",
    "entr_location": "entr_loc",
    "indoor_data": "indoor_dat",
}


def _as_str(value: Any) -> str:
    """字段转字符串。高德空字段会返回 [] 等非字符串，统一归一为空串。"""
    return value if isinstance(value, str) else ""


def _as_text(value: Any) -> str:
    """字段转文本：字符串原样；列表/元组逗号拼接；数字转字符串；其余归一空串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value if v not in (None, ""))
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def poi_to_row(poi: dict[str, Any]) -> dict[str, Any] | None:
    """单条 API POI → 输出行 dict（含 GCJ-02→WGS-84 转换）。无 id/坐标返回 None。"""
    poi_id = _as_str(poi.get("id"))
    location = _as_str(poi.get("location"))
    if not poi_id or "," not in location:
        return None
    try:
        lon_gcj02, lat_gcj02 = (float(v) for v in location.split(",", 1))
    except ValueError:
        return None
    lon, lat = gcj02_to_wgs84(lon_gcj02, lat_gcj02)

    # 嵌套字段：biz_ext 展平（评分 / 人均消费）、photos 取第一张、indoor_data 整体序列化
    biz_ext = poi.get("biz_ext")
    biz_rating = biz_cost = ""
    if isinstance(biz_ext, dict):
        biz_rating = _as_text(biz_ext.get("rating"))
        biz_cost = _as_text(biz_ext.get("cost"))

    photos = poi.get("photos")
    photo_title = photo_url = ""
    if isinstance(photos, list) and photos:
        first = photos[0]
        if isinstance(first, dict):
            # 官方文档字段名曾拼作 titile，实际返回为 title，两者都兼容
            photo_title = _as_str(first.get("title") or first.get("titile"))
            photo_url = _as_str(first.get("url"))

    indoor_data = poi.get("indoor_data")
    indoor_data_str = ""
    if isinstance(indoor_data, dict):
        indoor_data_str = json.dumps(indoor_data, ensure_ascii=False)

    return {
        # 坐标（lon/lat 为 WGS-84，写几何；lon_gcj02/lat_gcj02 为属性保留原值）
        "lon": round(lon, 7),
        "lat": round(lat, 7),
        "lon_gcj02": round(lon_gcj02, 7),
        "lat_gcj02": round(lat_gcj02, 7),
        # 基础字段
        "poi_id": poi_id,
        "name": _as_str(poi.get("name")),
        "poi_type": _as_str(poi.get("type")),
        "poi_type_code": _as_str(poi.get("typecode")),
        "cityname": _as_str(poi.get("cityname")),
        "adname": _as_str(poi.get("adname")),
        "address": _as_str(poi.get("address")),
        # 行政区层级
        "pname": _as_str(poi.get("pname")),
        "pcode": _as_str(poi.get("pcode")),
        "citycode": _as_str(poi.get("citycode")),
        "adcode": _as_str(poi.get("adcode")),
        # 联系与标识
        "tel": _as_text(poi.get("tel")),
        "alias": _as_text(poi.get("alias")),
        "biz_type": _as_text(poi.get("biz_type")),
        "parent": _as_str(poi.get("parent")),
        "navi_poiid": _as_str(poi.get("navi_poiid")),
        # 商业分析
        "business_area": _as_str(poi.get("business_area")),
        "tag": _as_text(poi.get("tag")),
        "biz_rating": biz_rating,
        "biz_cost": biz_cost,
        # 图片
        "photo_title": photo_title,
        "photo_url": photo_url,
        # 建筑 / 楼层 / 停车 / 入口
        "cpid": _as_str(poi.get("cpid")),
        "floor": _as_str(poi.get("floor")),
        "truefloor": _as_str(poi.get("truefloor")),
        "parking_type": _as_str(poi.get("parking_type")),
        "entr_location": _as_str(poi.get("entr_location")),
        # 联系信息与邮编
        "website": _as_text(poi.get("website")),
        "email": _as_text(poi.get("email")),
        "postcode": _as_str(poi.get("postcode")),
        # 室内地图
        "indoor_map": _as_text(poi.get("indoor_map")),
        "indoor_data": indoor_data_str,
        # grid_id 由调度层在入库时填充
    }
