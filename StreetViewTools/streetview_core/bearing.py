"""街道走向（bearing）与视角方位角计算。

与上游 SAGAI Block 2 一致：canonical bearing 归一化到 [0,180)，保证 left/right
与 OSM/矢量路网的数字化方向无关。

方位角只用 XY。这里刻意**不用** `shapely.ops.substring`——它对带 Z/M 的 3D 线
（CAD/DWG 转换的路网几乎都带）会抛「inhomogeneous shape」错误，改用
`project`（点到线的沿线距离）+ `interpolate`（沿线取点）求局部切线，二者
只返回纯 2D Point，天然免疫该缺陷。
"""

from __future__ import annotations

import math

try:
    from shapely import force_2d  # shapely 2.0+
except ImportError:  # pragma: no cover - 旧版 shapely 兜底
    from shapely.ops import transform as _transform

    def force_2d(geom):
        return _transform(lambda x, y, z=None: (x, y), geom)

# 四视角后缀（契约：{point_id}_{view}.jpg）
VIEW_SUFFIXES = ("left", "right", "front", "back")


def canonical_bearing(raw_bearing: float) -> float:
    """归一化到 [0,180)，保证 left/right 与路网数字化方向无关。"""
    b = raw_bearing % 360
    return b - 180 if b >= 180 else b


def compute_bearing_for_point(point_geom, street_geom, max_sub_length: float = 50.0) -> float:
    """采样点在街道上的 canonical bearing：取点沿街道的局部切线方向。

    用 `project` 得到采样点在街道上的沿线距离 d，再取 [d-half, d+half] 窗口两端点
    连线作为局部走向（half = max_sub_length/2）。只依赖 `interpolate`（纯 2D Point），
    因此对 3D/M/非法几何都安全。
    """
    street_geom = force_2d(street_geom)
    if street_geom.is_empty:
        return 0.0

    # MultiLineString / GeometryCollection → 取离点最近的 LineString 部件
    if street_geom.geom_type in ("MultiLineString", "GeometryCollection"):
        lines = [g for g in street_geom.geoms if g.geom_type == "LineString"]
        if not lines:
            return 0.0
        street_geom = min(lines, key=lambda g: g.distance(point_geom))

    # 点在街道上的沿线距离；非法几何（含 NaN 坐标）可能抛 GEOSException，统一兜底
    try:
        d = street_geom.project(point_geom)
        half = max_sub_length / 2.0
        d0 = max(0.0, d - half)
        d1 = min(street_geom.length, d + half)
        if d1 - d0 < 1e-9:
            return 0.0
        p0 = street_geom.interpolate(d0)
        p1 = street_geom.interpolate(d1)
        dx = p1.x - p0.x
        dy = p1.y - p0.y
    except Exception:
        return 0.0
    # 兜底：非法（NaN）或长度为零 → 0
    if not (math.isfinite(dx) and math.isfinite(dy)) or (abs(dx) < 1e-12 and abs(dy) < 1e-12):
        return 0.0
    return canonical_bearing(math.degrees(math.atan2(dx, dy)) % 360)


def get_headings_for_mode(mode: str, bearing: float = 0.0) -> list[tuple[float, str]]:
    """bearing 为 canonical [0,180)。left/right 为垂直街道方向，front/back 沿街道。

    mode ∈ {"left_right", "front_back", "4_views"}；其余视为 4_views。
    返回 [(方位角, suffix), ...]。
    """
    front = bearing % 360
    back = (bearing + 180) % 360
    left = (bearing + 270) % 360
    right = (bearing + 90) % 360
    if mode == "left_right":
        return [(left, "left"), (right, "right")]
    if mode == "front_back":
        return [(front, "front"), (back, "back")]
    return [(left, "left"), (right, "right"), (front, "front"), (back, "back")]
