"""瓦片网格坐标换算：百度 BD09MC + 高德 Web 墨卡托 XYZ。

百度走 geo_common.coords 的 bd09mc（LL2MC/MC2LL 分带多项式，与原项目一致）；
高德走标准 Web 墨卡托 XYZ（GCJ-02 坐标），此处新增换算函数。
"""

from __future__ import annotations

import math

from geo_common.coords import (bd09_to_wgs84, bd09mc_to_bd09, bd09_to_bd09mc,
                               gcj02_to_wgs84, wgs84_to_bd09, wgs84_to_gcj02)


def resolution(level: int, provider: str = "baidu") -> float:
    """米/像素（近似）。百度 = 2^(18−level)；高德用赤道 Web 墨卡托分辨率。"""
    if provider == "amap":
        return 156543.03392804097 / (2 ** level)
    return float(2 ** (18 - level))


# ------------------------------------------------------------------ 高德 Web 墨卡托 XYZ

def amap_world_px(lon: float, lat: float, level: int) -> tuple[float, float]:
    """WGS84 → 高德全局像素坐标（GCJ-02 + 标准 Web 墨卡托 XYZ）。"""
    glon, glat = wgs84_to_gcj02(lon, lat)
    n = 2 ** level
    gx = (glon + 180.0) / 360.0 * 256 * n
    gy = (1.0 - math.asinh(math.tan(math.radians(glat))) / math.pi) / 2.0 * 256 * n
    return gx, gy


def amap_px_to_wgs84(gx: float, gy: float, level: int) -> tuple[float, float]:
    """高德全局像素坐标 → WGS84（GCJ-02 → WGS84）。"""
    n = 256 * 2 ** level
    lon_gcj = gx / n * 360.0 - 180.0
    lat_gcj = math.degrees(
        math.atan(math.sinh(math.pi * (1.0 - 2.0 * gy / n))))
    return gcj02_to_wgs84(lon_gcj, lat_gcj)


# ------------------------------------------------------------------ 瓦片编号（provider 感知）

def wgs84_to_tile(lon: float, lat: float, level: int,
                  provider: str = "baidu") -> tuple[int, int]:
    """WGS84 点 → 瓦片号。百度走 BD09MC；高德走 Web 墨卡托 XYZ。"""
    if provider == "amap":
        gx, gy = amap_world_px(lon, lat, level)
        return math.floor(gx / 256), math.floor(gy / 256)
    mx, my = bd09_to_bd09mc(*wgs84_to_bd09(lon, lat))
    t = 256 * resolution(level)
    return math.floor(mx / t), math.floor(my / t)


def tiles_for_bbox(bounds: tuple[float, float, float, float],
                   level: int, provider: str = "baidu") -> list[tuple[int, int]]:
    """WGS84 (minx, miny, maxx, maxy) → 覆盖瓦片号集合。"""
    minx, miny, maxx, maxy = bounds
    tx0, ty0 = wgs84_to_tile(minx, miny, level, provider)
    tx1, ty1 = wgs84_to_tile(maxx, maxy, level, provider)
    return [(tx, ty) for tx in range(min(tx0, tx1), max(tx0, tx1) + 1)
            for ty in range(min(ty0, ty1), max(ty0, ty1) + 1)]


def tile_polygon(tx: int, ty: int, level: int, provider: str = "baidu"):
    """瓦片覆盖范围面（WGS84），供瓦片网格矢量图层导出。"""
    from shapely.geometry import Polygon

    if provider == "amap":
        n = 256 * 2 ** level
        gx0, gy0 = tx * 256, ty * 256
        corners = [amap_px_to_wgs84(gx0, gy0, level),
                   amap_px_to_wgs84(gx0 + 256, gy0, level),
                   amap_px_to_wgs84(gx0 + 256, gy0 + 256, level),
                   amap_px_to_wgs84(gx0, gy0 + 256, level)]
        return Polygon(corners)

    t = 256 * resolution(level)
    ox, oy = tx * t, ty * t
    corners = [bd09mc_to_bd09(ox, oy + t), bd09mc_to_bd09(ox + t, oy + t),
               bd09mc_to_bd09(ox + t, oy), bd09mc_to_bd09(ox, oy)]
    return Polygon([bd09_to_wgs84(*c) for c in corners])


def tile_key(level: int, tx: int, ty: int) -> str:
    """瓦片逻辑 id（进度/存档用），形如 "15_6605_1778"。"""
    return f"{level}_{tx}_{ty}"


def tile_key_parts(tid: str) -> tuple[int, int, int]:
    lv, tx, ty = tid.split("_")
    return int(lv), int(tx), int(ty)
