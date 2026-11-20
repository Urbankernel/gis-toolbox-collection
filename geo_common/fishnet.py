"""渔网 / 瓦片网格划分与递归四分（纯几何，无坐标系依赖）。

设计要点（与原 amap-poi-fetcher 的 grid.py 一致）：
- 网格坐标全程 float 保存（WGS-84），具体坐标系的「参数串格式化」由调用方负责；
- 渔网按范围几何的 bbox 规则划分，仅保留与几何有面积相交的网格；
- 四分裂产生 4 个子网格，gid 追加象限后缀（0=NW,1=NE,2=SW,3=SE），可溯源。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class Grid:
    """矩形网格（WGS-84 度）。xmin/ymin/xmax/ymax 分别为西/南/东/北边界。"""

    gid: str
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    depth: int = 0

    @property
    def edge(self) -> float:
        """网格短边长（度），用于最小网格判断。"""
        return min(self.xmax - self.xmin, self.ymax - self.ymin)

    @property
    def center(self) -> tuple[float, float]:
        return (self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2

    def to_polygon(self) -> Polygon:
        return box(self.xmin, self.ymin, self.xmax, self.ymax)


def _axis_count(span: float, size: float) -> int:
    """某方向上网格数。整除附近的浮点误差按整除处理，避免多划出一行/列。"""
    return max(1, int(math.ceil(span / size - 1e-9)))


def fishnet(geom: BaseGeometry, size: float) -> list[Grid]:
    """按 size（度）对 geom 的 bbox 划规则渔网，仅保留与 geom 有面积相交的网格。

    - 用整数索引计算边界（xmin + i*size），避免累加浮点误差导致整除时多出一列；
    - 边缘网格裁剪到 bbox 边界（可能小于 size），保证无缝覆盖、无重叠；
    - 面状几何按「相交面积 > 0」过滤；点/线等零面积几何回退为 intersects 判断。
    """
    if size <= 0:
        raise ValueError(f"grid size 必须为正数，当前: {size}")
    xmin, ymin, xmax, ymax = geom.bounds
    nx = _axis_count(xmax - xmin, size)
    ny = _axis_count(ymax - ymin, size)
    use_area_filter = geom.area > 0

    grids: list[Grid] = []
    for ix in range(nx):
        x, x2 = xmin + ix * size, min(xmin + (ix + 1) * size, xmax)
        for iy in range(ny):
            y, y2 = ymin + iy * size, min(ymin + (iy + 1) * size, ymax)
            cell = Grid(gid=f"g{ix}_{iy}", xmin=x, ymin=y, xmax=x2, ymax=y2)
            poly = cell.to_polygon()
            keep = poly.intersection(geom).area > 0 if use_area_filter else poly.intersects(geom)
            if keep:
                grids.append(cell)
    return grids


def split(grid: Grid) -> list[Grid]:
    """四分裂为 NW/NE/SW/SE 四个子网格，depth + 1，gid 追加象限后缀。"""
    mx = (grid.xmin + grid.xmax) / 2
    my = (grid.ymin + grid.ymax) / 2
    quads = [
        (grid.xmin, my, mx, grid.ymax),   # 0: NW
        (mx, my, grid.xmax, grid.ymax),   # 1: NE
        (grid.xmin, grid.ymin, mx, my),   # 2: SW
        (mx, grid.ymin, grid.xmax, my),   # 3: SE
    ]
    return [
        Grid(gid=f"{grid.gid}-{q}", xmin=a, ymin=b, xmax=c, ymax=d, depth=grid.depth + 1)
        for q, (a, b, c, d) in enumerate(quads)
    ]
