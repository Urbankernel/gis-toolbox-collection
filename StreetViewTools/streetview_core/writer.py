"""写采样点 / 下载报告要素类（复用 geo_common.exporter.write_points）。"""

from __future__ import annotations

from typing import Any, Sequence

from shapely.geometry import Point

# 采样点字段（工具 1 输出 = 工具 2 点要素类输入契约）
SAMPLE_FIELD_DEFS: list[tuple[str, str]] = [
    ("point_id", "TEXT"),
    ("street_id", "TEXT"),
    ("bearing", "DOUBLE"),
    ("lat", "DOUBLE"),
    ("lon", "DOUBLE"),
]

# 下载报告字段
REPORT_FIELD_DEFS: list[tuple[str, str]] = [
    ("point_id", "TEXT"),
    ("status", "TEXT"),
    ("lat", "DOUBLE"),
    ("lon", "DOUBLE"),
    ("panoid", "TEXT"),
    ("road_name", "TEXT"),
    ("shot_date", "TEXT"),
    ("car_heading", "DOUBLE"),
    ("img_left", "TEXT"),
    ("img_right", "TEXT"),
    ("img_front", "TEXT"),
    ("img_back", "TEXT"),
    ("img_pano", "TEXT"),
    ("message", "TEXT"),
]

# SHP 超长字段名映射（car_heading 11 字符 → 语义化短名）
REPORT_RENAME_MAP: dict[str, str] = {"car_heading": "car_head"}


def _to_features(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """把记录列表转成 write_points 需要的 features（含 WGS84 shapely 点几何）。"""
    feats = []
    for r in records:
        f = dict(r)
        f["geom"] = Point(r["lon"], r["lat"])
        feats.append(f)
    return feats


def write_samples(out_path: str, records: Sequence[dict[str, Any]],
                  spatial_ref: Any = None) -> str:
    """写采样点要素类（WGS84 点）。records 含 point_id/street_id/bearing/lat/lon。"""
    from geo_common.exporter import write_points

    feats = _to_features(records)
    return write_points(out_path, feats, SAMPLE_FIELD_DEFS,
                        spatial_ref=spatial_ref, layer_name="points")


def write_report(out_path: str, records: Sequence[dict[str, Any]],
                 spatial_ref: Any = None) -> str:
    """写下载报告要素类（WGS84 点）。"""
    from geo_common.exporter import write_points

    feats = _to_features(records)
    return write_points(out_path, feats, REPORT_FIELD_DEFS,
                        spatial_ref=spatial_ref, layer_name="report",
                        rename_map=REPORT_RENAME_MAP)
