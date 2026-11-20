"""干跑估算：不发起请求，估算瓦片数与预计耗时。"""

from __future__ import annotations

from .coords import resolution, tiles_for_bbox
from .providers import AUTO_LEVEL, auto_level


def resolve_level(area_km2: float, provider: str, level) -> int:
    """把 level（整数或 'auto'）解析为实际瓦片级别。"""
    if level == AUTO_LEVEL:
        return auto_level(area_km2, provider)
    return int(level)


def estimate(scope, provider: str, level, qps: float):
    """返回 (tile_count, est_seconds, resolved_level, meters_per_px)。"""
    lv = resolve_level(scope.area_km2(), provider, level)
    tiles = tiles_for_bbox(scope.bounds, lv, provider)
    n = len(tiles)
    est_seconds = n / qps if qps > 0 else float("inf")
    res = resolution(lv, provider)
    return n, est_seconds, lv, res
