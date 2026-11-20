"""多格式导出：CSV / JSON / Shapefile（点要素类，均为 WGS-84）。

改造自 cnemc-air 的 export.py：
- CSV：utf-8-sig，Excel 直开不乱码；缺失值写空字符串；
- JSON：规范化记录数组（缺失值保留 null）；
- SHP：每数据集一个 .shp，字段名受 10 字符限制（SHP_COLUMN_MAP 映射），
  用 geo_common.exporter.PointExporter 直写（替代 geopandas，零新增依赖）；
- 矢量属性表保留全部规范化字段（含 lon/lat 经纬度），几何取 WGS-84 的 lon/lat。
"""

from __future__ import annotations

import csv
import json
import logging
import os
from typing import Any

logger = logging.getLogger("cnemc_air.export")

# 成果 schema（列序固定，保证 CSV/属性表稳定，与原项目 cnemc-air 完全一致）
CITY_REALTIME_FIELDS = [
    "city_code", "city_name", "time_point",
    "aqi", "quality", "primary_pollutant", "measure", "unhealthful",
    "pm25", "pm10", "so2", "no2", "co", "o3",
    "co_level", "no2_level", "o3_level", "pm10_level", "pm2_5_level", "so2_level",
    "lon", "lat",
]
CITY_HOUR_FIELDS = [
    "city_code", "city_name", "time_point", "time_point_str",
    "aqi", "quality", "primary_pollutant", "measure", "unhealthful",
    "pm25", "pm10", "so2", "no2", "co", "o3",
    "lon", "lat",
]
CITY_DAY_FIELDS = [
    "city_code", "city_name", "day", "day_str",
    "aqi", "quality", "primary_pollutant", "measure", "unhealthful",
    "pm25_24h", "pm10_24h", "so2_24h", "no2_24h", "co_24h", "o3_8h_24h",
    "lon", "lat",
]
STATION_REALTIME_FIELDS = [
    "station_code", "station_name", "city_code", "city_name", "time_point",
    "aqi", "quality", "primary_pollutant", "measure", "unhealthful",
    "pm25", "pm10", "so2", "no2", "co", "o3", "o3_8h",
    "pm25_24h", "pm10_24h", "so2_24h", "no2_24h", "co_24h", "o3_24h", "o3_8h_24h",
    "co_level", "no2_level", "o3_level", "o3_8h_level", "pm10_level", "pm2_5_level", "so2_level",
    "no", "no_24h", "nox", "nox_24h",
    "lon", "lat",
]
STATION_HOUR_FIELDS = [
    "station_code", "station_name", "city_code", "city_name", "time_point", "time_point_str",
    "aqi", "quality", "primary_pollutant", "measure", "unhealthful",
    "pm25", "pm10", "so2", "no2", "co", "o3", "o3_8h",
    "pm25_24h", "pm10_24h", "so2_24h", "no2_24h", "co_24h", "o3_24h", "o3_8h_24h",
    "co_level", "no2_level", "o3_level", "o3_8h_level", "pm10_level", "pm2_5_level", "so2_level",
    "no", "no_24h", "nox", "nox_24h",
    "lon", "lat",
]

# 数值型字段（矢量导出时空值转 0，其余转 float）
_NUMERIC_FIELDS = {
    "aqi", "pm25", "pm10", "so2", "no2", "co", "o3", "o3_8h",
    "pm25_24h", "pm10_24h", "so2_24h", "no2_24h", "co_24h", "o3_24h", "o3_8h_24h",
    "co_level", "no2_level", "o3_level", "o3_8h_level",
    "pm10_level", "pm2_5_level", "so2_level",
    "no", "no_24h", "nox", "nox_24h",
}

# SHP 字段名 ≤10 字符：超长字段显式映射（CSV/JSON 无此限制，保持原名）
SHP_COLUMN_MAP = {
    "station_code": "stn_code",
    "station_name": "stn_name",
    "time_point_str": "time_pts",
    "primary_pollutant": "prim_poll",
    "unhealthful": "unhealth",
    "o3_8h_level": "o3_8h_lv",
    "pm2_5_level": "pm25_lv",
}

# 数据集定义：(结果键, 字段, 输出文件前缀)
_DATASETS: list[tuple[str, list[str], str]] = [
    ("city_realtime", CITY_REALTIME_FIELDS, "city_realtime"),
    ("city_hour", CITY_HOUR_FIELDS, "city_hour"),
    ("city_day", CITY_DAY_FIELDS, "city_day"),
    ("stations", STATION_REALTIME_FIELDS, "station_realtime"),
    ("station_hour", STATION_HOUR_FIELDS, "station_hour"),
]


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #
def _as_rows(value: Any) -> list[dict]:
    """把单条 dict / list / None 统一成行列表。"""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return []


def _field_defs(fields: list[str]) -> list[tuple[str, str]]:
    """字段列表 → [(name, type)]：lon/lat 与数值字段为 DOUBLE，其余 TEXT。"""
    defs = []
    for f in fields:
        typ = "DOUBLE" if (f in _NUMERIC_FIELDS or f in ("lon", "lat")) else "TEXT"
        defs.append((f, typ))
    return defs


def merge_results(results: list[dict]) -> dict:
    """把多个城市的 result 合并为聚合 result：各数据集行拼接。"""
    merged: dict[str, list[dict]] = {}
    for key, _fields, _prefix in _DATASETS:
        rows: list[dict] = []
        for r in results:
            rows.extend(_as_rows(r.get(key)))
        merged[key] = rows
    return merged


# --------------------------------------------------------------------------- #
# 表格类（CSV / JSON）
# --------------------------------------------------------------------------- #
def _write_csv(path: str, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def _write_json(path: str, fields: list[str], rows: list[dict]) -> None:
    out = [{k: r.get(k) for k in fields} for r in rows]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def export_table(result: dict, out_dir: str, *, csv_on: bool = True,
                 json_on: bool = True) -> list[str]:
    """导出 CSV + JSON（写到 out_dir），返回产物路径列表。

    空数据集（无数据行）跳过，不产生只有表头的空文件。
    """
    outputs: list[str] = []
    for key, fields, prefix in _DATASETS:
        rows = _as_rows(result.get(key))
        if not rows:
            logger.info("%s: 0 行（跳过 CSV/JSON）", prefix)
            continue
        if csv_on:
            p = os.path.join(out_dir, f"{prefix}.csv")
            _write_csv(p, fields, rows)
            outputs.append(p)
        if json_on:
            p = os.path.join(out_dir, f"{prefix}.json")
            _write_json(p, fields, rows)
            outputs.append(p)
    return outputs


# --------------------------------------------------------------------------- #
# 矢量类（SHP，PointExporter 替代 geopandas）
# --------------------------------------------------------------------------- #
def _clean_row(row: dict, field_defs: list[tuple[str, str]]) -> dict:
    """SHP 数值字段不允许 NULL：数值字段 None → 0，文本字段 None → ''。"""
    out = dict(row)
    for name, typ in field_defs:
        if typ.upper() == "TEXT":
            out[name] = "" if out.get(name) is None else str(out[name])
        elif out.get(name) is None:
            out[name] = 0
    return out


def export_vector(result: dict, out_dir: str) -> list[str]:
    """导出 SHP 点要素类（每数据集一个 .shp，写到 out_dir），返回路径列表。"""
    from geo_common.exporter import PointExporter

    outputs: list[str] = []
    for key, fields, prefix in _DATASETS:
        rows = _as_rows(result.get(key))
        # 缺失坐标的行跳过（无法落点）
        valid = [r for r in rows
                 if r.get("lon") is not None and r.get("lat") is not None]
        if not valid:
            logger.info("%s: 0 行（跳过）", prefix)
            continue
        fdefs = _field_defs(fields)
        out = os.path.join(out_dir, prefix + ".shp")
        exp = PointExporter(out, fdefs, rename_map=SHP_COLUMN_MAP)
        try:
            exp.create(overwrite=True)
            exp.write_batch(_clean_row(r, fdefs) for r in valid)
        finally:
            exp.close()
        outputs.append(out)
        logger.info("SHP 导出: %s（%d 行）", out, len(valid))
    return outputs
