"""IDW 插值与等时带提取。

职责：IDW 插值（scipy KDTree 实现，替代 arcpy.sa.Idw，范围外扩后裁剪以抑制
边界效应）→ 按可配置阈值提取等值线 → 转 shapely 等时带面。

从原项目 interpolate.py 移植，唯一改动：`affine.Affine` 换成 6 元组 geotransform
（(xmin, dx, 0, ymax, 0, -dy)，GDAL 约定），消除 affine 依赖。
"""

from __future__ import annotations

import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from .coords import meters_to_degrees


def idw_interpolate(
    xs: np.ndarray,
    ys: np.ndarray,
    values: np.ndarray,
    mask_polygon: BaseGeometry,
    cell_size: float = 10.0,
    power: float = 2.0,
    k: int = 12,
    expand: float = 200.0,
) -> tuple[np.ma.MaskedArray, tuple]:
    """反距离权重插值，返回（掩膜栅格, geotransform）。

    - `cell_size` 与 `expand` 单位均为**米**（按研究区中心纬度换算经纬度步长）；
    - 插值范围在研究区外接矩形上外扩 expand，之后用研究区面掩膜；
    - 栅格行方向自北向南（与 GeoTIFF / arcpy NumPyArrayToRaster 约定一致）。
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(xs) < 3:
        raise ValueError(f"有效采样点不足（{len(xs)} < 3），无法插值")

    minx, miny, maxx, maxy = mask_polygon.bounds
    lon_c, lat_c = (minx + maxx) / 2, (miny + maxy) / 2
    ex, ey = meters_to_degrees(lon_c, lat_c, expand)
    minx -= ex
    miny -= ey
    maxx += ex
    maxy += ey

    cx, cy = meters_to_degrees(lon_c, lat_c, cell_size)
    ncols = max(2, int(np.ceil((maxx - minx) / cx)))
    nrows = max(2, int(np.ceil((maxy - miny) / cy)))
    cell_x = (maxx - minx) / ncols
    cell_y = (maxy - miny) / nrows

    gx = minx + (np.arange(ncols) + 0.5) * cell_x
    gy = maxy - (np.arange(nrows) + 0.5) * cell_y  # 自北向南
    mx, my = np.meshgrid(gx, gy)
    grid_points = np.column_stack([mx.ravel(), my.ravel()])

    tree = cKDTree(np.column_stack([xs, ys]))
    kk = min(k, len(xs))
    dists, idxs = tree.query(grid_points, k=kk)

    with np.errstate(divide="ignore"):
        weights = 1.0 / np.power(dists, power)
    exact = ~np.isfinite(weights).all(axis=1)
    weights[~np.isfinite(weights)] = 0.0
    wsum = weights.sum(axis=1)
    interp = np.where(
        wsum > 0,
        (weights * values[idxs]).sum(axis=1) / np.where(wsum > 0, wsum, 1.0),
        0.0,
    )
    if exact.any():
        first = idxs[exact, 0]
        interp[exact] = values[first]

    grid = interp.reshape(nrows, ncols)
    inside = shapely.contains_xy(mask_polygon, mx, my)
    masked = np.ma.masked_where(~inside, grid)

    # geotransform：GDAL 约定 (xmin, dx, 0, ymax, 0, -dy)
    gt = (minx, cell_x, 0.0, maxy, 0.0, -cell_y)
    return masked, gt


def extract_contours(
    grid: np.ma.MaskedArray,
    transform: tuple,
    thresholds: list[float],
    clip_polygon: BaseGeometry | None = None,
    keep_over: bool = True,
) -> list[dict]:
    """按阈值提取等时带面（contourf 填充带）。

    transform 为 6 元组 geotransform (xmin, dx, 0, ymax, 0, -dy)。
    返回 [{"lo": float, "hi": float | None, "geometry": shapely 面}, ...]。
    最内圈（0~首阈值）也会生成；keep_over=True 时额外生成最外圈开放带（hi=None）。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    levels = [0.0] + [float(t) for t in thresholds]
    nrows, ncols = grid.shape
    cols, rows = np.meshgrid(np.arange(ncols), np.arange(nrows))
    # geotransform 解包
    xs = transform[0] + (cols + 0.5) * transform[1]
    ys = transform[3] + (rows + 0.5) * transform[5]

    z = grid.filled(np.nan)

    fig = plt.figure()
    try:
        cs = plt.contourf(
            xs, ys, z, levels=levels,
            extend="max" if keep_over else "neither",
        )
        bands: list[dict] = []
        all_levels = list(cs.levels)

        for i, segs in enumerate(cs.allsegs):
            lo = float(all_levels[i])
            hi = float(all_levels[i + 1]) if i + 1 < len(all_levels) else None

            polys = []
            for seg in segs:
                if len(seg) < 4:
                    continue
                poly = Polygon(seg)
                if not poly.is_valid:
                    poly = shapely.make_valid(poly)
                if poly.geom_type == "Polygon" and poly.area > 0:
                    polys.append(poly)
                elif poly.geom_type == "MultiPolygon":
                    polys.extend([p for p in poly.geoms if p.area > 0])
            if not polys:
                continue

            # 环形带洞修复：contourf 把环形带的「洞边界」也当闭合线段返回，
            # 直接填充会变成实心盘（长时段带盖住短时段带）。按包含关系分组。
            outers, holes = [], []
            for p in sorted(polys, key=lambda g: g.area, reverse=True):
                if any(o.contains(p) for o in outers):
                    holes.append(p)
                else:
                    outers.append(p)

            if outers and holes:
                band: BaseGeometry = shapely.union_all(outers).difference(
                    shapely.union_all(holes))
            elif len(outers) == 1:
                band = outers[0]
            else:
                # 同一档的分片彼此共边，必须先 dissolve 合并
                band = shapely.union_all(outers)

            if clip_polygon is not None:
                if not clip_polygon.is_valid:
                    clip_polygon = shapely.make_valid(clip_polygon)
                band = band.intersection(clip_polygon)
            if band.is_empty:
                continue
            bands.append({"lo": lo, "hi": hi, "geometry": band})

        return bands
    finally:
        plt.close(fig)


def band_label(band: dict) -> str:
    """等时带标签（不含单位）：hi=None 的开放带显示为 ">lo"，如 ">30"。"""
    if band.get("hi") is None:
        return f">{band['lo']:g}"
    return f"{band['lo']:g}-{band['hi']:g}"


def split_reachable(points, values: list[float | None]):
    """拆分可达/不可达点。

    points: list[GridPoint]；values: 与 points 等长的 duration 列表（None = 不可达）。
    返回 (rx, ry, rv, unreachable_points)。
    """
    rx, ry, rv, unreachable = [], [], [], []
    for p, v in zip(points, values):
        if v is None:
            unreachable.append(p)
        else:
            rx.append(p.lon)
            ry.append(p.lat)
            rv.append(float(v))
    return (np.array(rx), np.array(ry), np.array(rv), unreachable)


def merge_point_values(per_origin_values: dict[int, list]) -> list[float | None]:
    """多出发点时间值逐点合并：取到所有出发点时间的最小值（全不可达则为 None）。"""
    ids = sorted(per_origin_values)
    if not ids:
        return []
    n = len(per_origin_values[ids[0]])
    merged: list[float | None] = []
    for i in range(n):
        vals = [per_origin_values[oi][i] for oi in ids if per_origin_values[oi][i] is not None]
        merged.append(min(vals) if vals else None)
    return merged


def merge_time_grids(grids: list[np.ma.MaskedArray]) -> np.ma.MaskedArray:
    """合并多个出发点的时间栅格：逐像元取最小值。

    前提：各栅格须同 shape、同 geotransform（由同一研究区 + 同一插值配置保证）。
    """
    if not grids:
        raise ValueError("merge_time_grids 至少需要一个栅格")
    shapes = {g.shape for g in grids}
    if len(shapes) != 1:
        raise ValueError(f"各出发点栅格 shape 不一致，无法叠加合并: {shapes}")
    stacked = np.ma.stack(grids)
    return stacked.min(axis=0)
