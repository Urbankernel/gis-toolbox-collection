"""GeoAtlas 响应归一化：字段提取、特殊要素处理、几何修复。

标准输出字段（均 <= 10 字符，保证 shp 字段名安全）：
    adcode, name, level, parent, children, cen_lng, cen_lat

近原样移植自 Urbankernel/chinaboundary。几何以 GeoJSON dict 表示（无 arcpy 依赖），
导出层用 shapely.geometry.shape() 桥接回 shapely 再交给 geo_common.exporter。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("chinaboundary.normalize")

SPECIAL_ADCODE_SUFFIX = "_JD"  # 南海诸岛十段线等特殊要素（adcode 形如 100000_JD）

OUTPUT_FIELDS = ["adcode", "name", "level", "parent", "children", "cen_lng", "cen_lat"]


def _fix_geometry(geometry: dict):
    """解析几何并用 buffer(0) 修复无效多边形；失败/为空返回 None。"""
    from shapely.geometry import mapping, shape  # 延迟导入，避免 Pro 进程 shapely 循环导入
    try:
        geom = shape(geometry)
    except Exception as e:
        logger.warning("几何解析失败：%s", e)
        return None
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
        if geom.is_empty:
            return None
    return mapping(geom)


def parse_feature(feature: dict, include_special: bool = False):
    """把 GeoAtlas 单个 Feature 归一化为标准 dict；特殊要素按开关处理。"""
    props = feature.get("properties") or {}
    adcode = props.get("adcode")
    if adcode is None or str(adcode).endswith(SPECIAL_ADCODE_SUFFIX):
        if not include_special:
            logger.debug("跳过特殊要素: %s", adcode)
            return None
    geometry = feature.get("geometry")
    if not geometry or not geometry.get("coordinates"):
        logger.warning("feature 缺少几何，跳过：%s", props.get("name"))
        return None
    geom = _fix_geometry(geometry)
    if geom is None:
        return None

    parent = props.get("parent") or {}
    parent_adcode = parent.get("adcode")
    center = props.get("center") or []

    return {
        "adcode": str(adcode),
        "name": props.get("name") or "",
        "level": props.get("level"),
        "parent": str(parent_adcode) if parent_adcode is not None else None,
        "children": props.get("childrenNum"),
        "cen_lng": center[0] if len(center) > 0 else None,
        "cen_lat": center[1] if len(center) > 1 else None,
        "geometry": geom,
    }


def parse_response(data: dict | None, include_special: bool = False) -> list[dict]:
    """解析 GeoAtlas FeatureCollection 响应，返回标准 feature 列表。"""
    if not data:
        return []
    out = []
    for f in data.get("features") or []:
        parsed = parse_feature(f, include_special)
        if parsed is not None:
            out.append(parsed)
    return out
