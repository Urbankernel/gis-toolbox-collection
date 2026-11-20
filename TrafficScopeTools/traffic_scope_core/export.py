"""成果导出：单波段分类栅格 GeoTIFF + 瓦片网格矢量（WGS84）。

- 栅格：arcpy.NumPyArrayToRaster + DefineProjection(4326) + CopyRaster
  （NumPyArrayToRaster 产出栅格无空间参考，必须补坐标系，见项目铁律）。
- 矢量：复用 geo_common.exporter.write_polygons。
"""

from __future__ import annotations

import os

from geo_common.exporter import write_polygons

from .coords import tile_key_parts
from .providers import PROVIDERS
from .tiles import fit_geotransform

TILE_FIELD_DEFS: list[tuple[str, str]] = [
    ("tile_id", "TEXT"),
    ("level", "LONG"),
    ("tx", "LONG"),
    ("ty", "LONG"),
]


def write_classified_geotiff(status, mosaic, out_tif: str,
                             provider: str = "baidu") -> str:
    """单波段四级分类栅格 → GeoTIFF（uint8，nodata=0，EPSG:4326，附调色板）。"""
    import arcpy

    xmin, dx, _, ymax, _, mdy = fit_geotransform(mosaic)
    dy = -mdy                       # 向北为正
    nrows, ncols = status.shape
    lower_left = arcpy.Point(xmin, ymax - nrows * dy)

    os.makedirs(os.path.dirname(os.path.abspath(out_tif)), exist_ok=True)
    arcpy.env.overwriteOutput = True

    sr = arcpy.SpatialReference(4326)   # WGS-84
    raster = arcpy.NumPyArrayToRaster(
        status.astype("uint8"), lower_left, dx, dy, value_to_nodata=0)
    arcpy.management.DefineProjection(raster, sr)   # 补坐标系标签（不重投影）
    arcpy.management.CopyRaster(raster, out_tif, nodata_value=0)

    _try_add_colormap(out_tif, provider)
    return out_tif


def _try_add_colormap(out_tif: str, provider: str) -> None:
    """给分类栅格加四级调色板（失败不影响成果，仅无配色）。"""
    import tempfile

    import arcpy

    palette = PROVIDERS[provider]["palette"]
    lines = []
    for code in (1, 2, 3, 4):
        r, g, b = palette[code]
        lines.append(f"{code} {r} {g} {b}")
    try:
        fd, clr = tempfile.mkstemp(suffix=".clr")
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write("\n".join(lines) + "\n")
        arcpy.management.AddColormap(out_tif, input_CLR_file=clr)
    except Exception:
        pass
    finally:
        try:
            if "clr" in dir():
                os.remove(clr)
        except Exception:
            pass


def export_tiles(tile_pairs: list, out_path: str) -> str:
    """路况瓦片网格 → 矢量（shp/gpkg，质检漏抓用）。tile_pairs 为 (tid, polygon)。"""
    if not tile_pairs:
        return out_path
    features = []
    for tid, poly in tile_pairs:
        lv, tx, ty = tile_key_parts(tid)
        features.append({
            "tile_id": tid, "level": lv, "tx": tx, "ty": ty, "geom": poly,
        })
    return write_polygons(out_path, features, TILE_FIELD_DEFS,
                          layer_name="query_tiles")
