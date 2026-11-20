"""等时圈专用地理换算：米 ↔ 度、球面距离。

坐标互转（GCJ-02 / BD-09 / WGS-84）统一复用 geo_common.coords，本模块只补
原项目依赖 pyproj 的两个函数，用球面近似替代（原项目注释已说明二者等价，
球面近似对渔网间距 / 栅格分辨率这类「米级参数」误差可忽略）。
"""

from __future__ import annotations

import math

# 复用共享坐标转换（WGS-84 ↔ GCJ-02 等）
from geo_common.coords import gcj02_to_wgs84, wgs84_to_gcj02  # noqa: F401

# WGS-84 每度纬度对应的米数（中纬度近似，误差 < 0.5%）
_METERS_PER_DEG_LAT = 111320.0


def meters_to_degrees(lon: float, lat: float, meters: float) -> tuple[float, float]:
    """把「米」换算为经纬度差（度）：返回 (经度差, 纬度差)。

    在 (lon, lat) 处按正东 / 正北方向计算。纬度差恒定；经度差随纬度收窄（cosφ）。
    替代原项目 pyproj.Geod.fwd 的椭球精算，球面近似等价（原项目注释确认）。
    """
    lat_d = meters / _METERS_PER_DEG_LAT
    lon_d = meters / (_METERS_PER_DEG_LAT * math.cos(math.radians(lat)))
    return abs(lon_d), abs(lat_d)


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """两点球面距离（公里），用于自适应渔网与干跑估算。"""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
