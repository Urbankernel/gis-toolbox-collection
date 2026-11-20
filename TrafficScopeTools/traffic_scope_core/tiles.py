"""路况瓦片采集与分类：下载 PNG → 四级最近邻分类 → 镶嵌拼接 → 范围裁剪。

从原项目 tiles.py 移植，唯一改动：
- 移除 assemble_raw_mosaic / clip_rgba_to_polygon / status_to_rgba（RGBA 原始
  拼接已按需求砍掉，ArcGIS 无 A 通道、白底显示效果差）；
- `fit_affine`（rasterio.Affine）改为 `fit_geotransform`（6 元组 GDAL
  geotransform），供 arcpy.NumPyArrayToRaster 使用；
- 请求层复用 geo_common.ratelimit（qps 限速在 pipeline 层，退避在此处）。
"""

from __future__ import annotations

import io
import math
import time

import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from . import coords as C
from .providers import DIST_THRESHOLD, MIN_ALPHA, PROVIDERS


# ------------------------------------------------------------------ 采集

def fetch_tile_png(tx: int, ty: int, level: int, provider: str = "baidu",
                   timeout: float = 15.0, max_retries: int = 3) -> bytes:
    """下载一个路况瓦片，返回 PNG 原始字节（指数退避）。"""
    import requests

    meta = PROVIDERS[provider]
    hdrs = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36"),
            "Referer": meta["referer"]}
    if provider == "amap":
        url = f"{meta['url']}?v=1.0&x={tx}&y={ty}&z={level}"
        kwargs = {"url": url, "headers": hdrs, "timeout": timeout}
    else:
        us = int(time.time() * 1e6)
        params = {"level": level, "x": tx, "y": ty, "time": us,
                  "smallflow": 1, "scaler": 1}
        kwargs = {"url": meta["url"], "params": params, "headers": hdrs,
                  "timeout": timeout}

    last: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(**kwargs)
            r.raise_for_status()
            if r.content[:8] != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError(f"非 PNG 响应（{len(r.content)} 字节）")
            return r.content
        except Exception as e:
            last = e
            if attempt < max_retries:
                time.sleep(min(2 ** (attempt + 1), 10))
    raise RuntimeError(f"{provider} 瓦片 {level}/{tx}/{ty} 下载失败: {last}")


# ------------------------------------------------------------------ 分类

def classify_tile(png_bytes: bytes, provider: str = "baidu") -> np.ndarray:
    """PNG 瓦片 → 路况等级矩阵（uint8，256×256）。

    0=无数据/透明，1=畅通，2=缓行，3=拥堵，4=严重拥堵。最近邻参考色 +
    距离阈值；半透明像素（alpha ≤ MIN_ALPHA）直接判无数据。
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    arr = np.asarray(img, dtype=np.int32)
    a = arr[..., 3]
    valid = a > MIN_ALPHA
    out = np.zeros(arr.shape[:2], dtype=np.uint8)
    if not valid.any():
        return out

    rgb = arr[..., :3]
    palette = PROVIDERS[provider]["palette"]
    refs = np.array([palette[i] for i in (1, 2, 3, 4)], dtype=np.int32)  # (4,3)
    diff = rgb[:, :, None, :] - refs[None, None, :, :]     # (H, W, 4, 3)
    dist = (diff ** 2).sum(axis=-1)                        # (H, W, 4)
    best = dist.argmin(axis=-1)
    best_dist = dist.min(axis=-1)
    hit = valid & (best_dist < DIST_THRESHOLD)
    out[hit] = best[hit] + 1
    return out


# ------------------------------------------------------------------ 镶嵌

class Mosaic:
    """分级矩阵镶嵌体：分级数组 + 像素→WGS84 精确映射。"""

    def __init__(self, status: np.ndarray, level: int, tx0: int, ty_top: int,
                 provider: str = "baidu"):
        self.status = status
        self.level = level
        self.provider = provider
        self.tx0 = tx0
        self.ty_top = ty_top            # 顶部（最北）瓦片号
        self.res = (float(2 ** (18 - level)) if provider == "baidu"
                    else 156543.03392804097 / (2 ** level))
        if provider == "amap":
            self.ox = self.oy = None    # 高德走全局像素坐标
        else:
            t = 256 * self.res
            self.ox = tx0 * t
            self.oy = (ty_top + 1) * t

    @property
    def shape(self) -> tuple[int, int]:
        return self.status.shape

    def pixel_to_wgs84(self, px: float, py: float) -> tuple[float, float]:
        if self.provider == "amap":
            n = 256 * 2 ** self.level
            gx = self.tx0 * 256 + px
            gy = self.ty_top * 256 + py
            return C.amap_px_to_wgs84(gx, gy, self.level)
        mx = self.ox + px * self.res
        my = self.oy - py * self.res
        return C.bd09_to_wgs84(*C.bd09mc_to_bd09(mx, my))

    def wgs84_to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        if self.provider == "amap":
            gx, gy = C.amap_world_px(lon, lat, self.level)
            return gx - self.tx0 * 256, gy - self.ty_top * 256
        mx, my = C.bd09_to_bd09mc(*C.wgs84_to_bd09(lon, lat))
        return (mx - self.ox) / self.res, (self.oy - my) / self.res


def assemble_mosaic(level: int, classified: dict[tuple[int, int], np.ndarray],
                    provider: str = "baidu") -> Mosaic:
    """各瓦片分级矩阵拼成一张大矩阵，返回 Mosaic。

    百度 ty 向北递增（ty 大放顶部/行 0）；高德标准 XYZ y 向南递增（ty 小放顶部）。
    """
    if not classified:
        raise ValueError("没有可拼接的瓦片")
    tx0 = min(tx for tx, _ in classified)
    tx1 = max(tx for tx, _ in classified)
    ty_min = min(ty for _, ty in classified)
    ty_max = max(ty for _, ty in classified)
    w = (tx1 - tx0 + 1) * 256
    h = (ty_max - ty_min + 1) * 256
    status = np.zeros((h, w), dtype=np.uint8)
    ty_top = ty_min if provider == "amap" else ty_max
    for (tx, ty), arr in classified.items():
        x0 = (tx - tx0) * 256
        y0 = ((ty - ty_min) if provider == "amap" else (ty_max - ty)) * 256
        status[y0:y0 + 256, x0:x0 + 256] = arr
    return Mosaic(status, level, tx0, ty_top, provider)


# ------------------------------------------------------------------ 范围裁剪

def _inside_mask(mosaic: Mosaic, polygon: BaseGeometry) -> np.ndarray:
    """查询范围多边形 → 像素中心是否在内的布尔掩膜（H×W）。

    MultiPolygon 逐岛单独建 Path 再取并集，避免多岛/洞顶点串联自交、
    奇偶规则误判；有洞的 Polygon 用 exterior 减 interiors。
    """
    from matplotlib.path import Path

    h, w = mosaic.status.shape
    geoms = [polygon] if polygon.geom_type == "Polygon" else list(polygon.geoms)

    def _to_path(coords):
        return Path([mosaic.wgs84_to_pixel(x, y) for x, y in coords])

    islands = []
    for geom in geoms:
        ext = list(geom.exterior.coords)
        if len(ext) < 3:
            continue
        holes = [_to_path(list(ring.coords))
                 for ring in geom.interiors if len(ring.coords) >= 3]
        islands.append((_to_path(ext), holes))

    if not islands:
        return np.ones((h, w), dtype=bool)

    inside = np.zeros((h, w), dtype=bool)
    for y in range(h):
        pts = np.column_stack([np.arange(w, dtype=np.float64) + 0.5,
                               np.full(w, y + 0.5, dtype=np.float64)])
        row_inside = np.zeros(w, dtype=bool)
        for ex_path, hole_paths in islands:
            island = ex_path.contains_points(pts)
            for hp in hole_paths:
                island &= ~hp.contains_points(pts)
            row_inside |= island
        inside[y] = row_inside
    return inside


def clip_to_polygon(mosaic: Mosaic, polygon: BaseGeometry) -> None:
    """原地把镶嵌矩阵裁剪到查询范围：范围外像素置 0。"""
    inside = _inside_mask(mosaic, polygon)
    mosaic.status[~inside] = 0


# ------------------------------------------------------------------ 定位

def _solve(xs, ys, vals):
    """正规方程最小二乘拟合 vals ≈ a*x + b*y + c（纯 Python，避免 BLAS）。"""
    n = len(xs)
    Sx = sum(xs); Sy = sum(ys)
    Sxx = sum(x * x for x in xs)
    Syy = sum(y * y for y in ys)
    Sxy = sum(x * y for x, y in zip(xs, ys))
    Sxv = sum(x * v for x, v in zip(xs, vals))
    Syv = sum(y * v for y, v in zip(ys, vals))
    Sv = sum(vals)
    det = (Sxx * (Syy * n - Sy * Sy)
           - Sxy * (Sxy * n - Sy * Sx)
           + Sx * (Sxy * Sy - Syy * Sx))
    if abs(det) < 1e-12:
        return 0.0, 0.0, 0.0
    a = (Sxv * (Syy * n - Sy * Sy)
         - Sxy * (Syv * n - Sy * Sv)
         + Sx * (Syv * Sy - Syy * Sv)) / det
    b = (Sxx * (Syv * n - Sy * Sv)
         - Sxv * (Sxy * n - Sy * Sx)
         + Sx * (Sxy * Sv - Syv * Sx)) / det
    c = (Sxx * (Syy * Sv - Syv * Sy)
         - Sxy * (Sxy * Sv - Syv * Sx)
         + Sxv * (Sxy * Sy - Syy * Sx)) / det
    return a, b, c


def fit_geotransform(mosaic: Mosaic) -> tuple[float, float, float, float, float, float]:
    """像素→WGS84 的轴对齐 geotransform（忽略旋转项，城市尺度残差 <1 m）。

    返回 GDAL 6 元组 (xmin, dx, 0, ymax, 0, dy)，dy < 0（北向上）。
    """
    h, w = mosaic.status.shape
    cols = np.linspace(0, w, 5)
    rows = np.linspace(0, h, 5)
    xs, ys, lons, lats = [], [], [], []
    for py in rows:
        for px in cols:
            lon, lat = mosaic.pixel_to_wgs84(px, py)
            xs.append(float(px)); ys.append(float(py))
            lons.append(lon); lats.append(lat)
    a, _b, c = _solve(xs, ys, lons)   # lon ≈ a*px + b*py + c
    _d, e, f = _solve(xs, ys, lats)   # lat ≈ d*px + e*py + f
    xmin, dx = c, a
    ymax, dy = f, e
    return (xmin, dx, 0.0, ymax, 0.0, dy)
