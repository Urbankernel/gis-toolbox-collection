"""街景批量下载主流程（逆向接口，无 AK，去掉 pandas/geopandas 依赖）。

流程：采样点 WGS84 → BD09MC（geo_common.coords.wgs84_to_bd09mc）→
      qsdata 拿 panoid → sdata 拿 Heading/路名/拍摄日期 → pdata 拼全景 →
      按 bearing 切四视角（或直接输出全景）→ 存 JPG + 写报告。

采样点输入两种（二选一）：
  - CSV：name,lng,lat[,bearing]（points_crs 可指定 wgs84/gcj02/bd09，自动转 WGS84）
  - 点要素类：SHAPE@ 取坐标 + 可选 point_id / bearing 字段
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from geo_common.coords import bd09_to_wgs84, gcj02_to_wgs84, wgs84_to_bd09mc

from .bearing import get_headings_for_mode
from .pano_client import BaiduReverseClient
from .pano_slice import crop_streetview, slice_views

# 文件系统非法字符（Windows 文件名不允许 \ / : * ? " < > |，连同空白一并去掉）
_ILLEGAL_FS = re.compile(r'[\\/:*?"<>|\s]+')
_DATE8 = re.compile(r"^\d{8}$")

# 报告字段（与 writer.REPORT_FIELD_DEFS 一致，写 CSV 用）
REPORT_FIELDS = ["point_id", "status", "lat", "lon", "panoid",
                 "road_name", "shot_date", "car_heading",
                 "img_left", "img_right", "img_front", "img_back", "img_pano",
                 "message"]


def _safe_road_name(name: str | None) -> str:
    """把路名清洗成可入文件名的安全串；空/无则返回 ''。"""
    if not name:
        return ""
    return _ILLEGAL_FS.sub("", str(name))


def _img_stem(pid: str, road_name: str | None, shot_date: str | None, suffix: str) -> str:
    """图片文件名主干：{pid}_{路名}_{拍摄时间}_{suffix}（可选段缺失则省略）。"""
    meta = "_".join(x for x in (_safe_road_name(road_name), _safe_road_name(shot_date)) if x)
    return f"{pid}_{meta}_{suffix}" if meta else f"{pid}_{suffix}"


def _find_image(download_dir: Path, pid: str, suffix: str) -> str | None:
    """返回该点某方向的图片实际路径（兼容带/不带路名两种命名）；不存在返回 None。"""
    cand = sorted(download_dir.glob(f"{pid}_*_{suffix}.jpg"))
    if cand:
        return str(cand[0])
    legacy = download_dir / f"{pid}_{suffix}.jpg"
    return str(legacy) if legacy.exists() else None


def _has_image(download_dir: Path, pid: str, suffix: str) -> bool:
    """该点某方向图片是否存在。"""
    return _find_image(download_dir, pid, suffix) is not None


def _parse_road_date(download_dir: Path, pid: str, suffixes) -> tuple[str | None, str | None]:
    """从已下载文件名解析路名与拍摄日期（断点续跑恢复报告信息用）。"""
    for s in suffixes:
        cand = sorted(download_dir.glob(f"{pid}_*_{s}.jpg"))
        legacy = download_dir / f"{pid}_{s}.jpg"
        path = cand[0] if cand else (legacy if legacy.exists() else None)
        if path is None:
            continue
        stem = path.stem
        if stem == f"{pid}_{s}":
            return None, None
        meta = stem[len(pid) + 1:]
        if meta.endswith("_" + s):
            meta = meta[:-len(s) - 1]
        parts = meta.split("_") if meta else []
        if not parts:
            return None, None
        if _DATE8.match(parts[-1]):
            return "_".join(parts[:-1]) or None, parts[-1]
        return "_".join(parts) or None, None
    return None, None


# ------------------------------------------------------------------ 采样点加载

def load_points_from_csv(points_csv: str, points_crs: str = "wgs84") -> list[dict[str, Any]]:
    """读采样点 CSV（name,lng,lat[,bearing]），返回 list[dict]（WGS84）。

    points_crs 仅 wgs84/gcj02/bd09，非 wgs84 时逐点逆变换到 WGS84。
    """
    pts: list[dict[str, Any]] = []
    with open(points_csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            lng = float(row["lng"])
            lat = float(row["lat"])
            b = row.get("bearing")
            bearing = float(b) if b not in (None, "") else 0.0
            if points_crs == "gcj02":
                lng, lat = gcj02_to_wgs84(lng, lat)
            elif points_crs == "bd09":
                lng, lat = bd09_to_wgs84(lng, lat)
            elif points_crs != "wgs84":
                raise ValueError(f"未知坐标系 {points_crs!r}，仅支持 wgs84/gcj02/bd09")
            pts.append({"point_id": name, "lng": lng, "lat": lat, "bearing": bearing})
    return pts


def load_points_from_fc(points_fc: str) -> list[dict[str, Any]]:
    """读采样点点要素类，返回 list[dict]（坐标统一 projectAs 到 WGS84）。

    契约：SHAPE@ 取坐标；可选字段 point_id（唯一标识，缺省用序号）、bearing（方位角，缺省 0）。
    """
    import arcpy

    desc = arcpy.Describe(points_fc)
    names = [f.name for f in desc.fields]
    id_field = "point_id" if "point_id" in names else None
    bearing_field = "bearing" if "bearing" in names else None

    cursor_fields = ["SHAPE@"]
    if id_field:
        cursor_fields.append(id_field)
    if bearing_field:
        cursor_fields.append(bearing_field)

    wgs84 = arcpy.SpatialReference(4326)
    pts: list[dict[str, Any]] = []
    with arcpy.da.SearchCursor(points_fc, cursor_fields) as cur:
        for i, row in enumerate(cur):
            g_wgs = row[0].projectAs(wgs84)
            lon, lat = g_wgs.firstPoint.X, g_wgs.firstPoint.Y
            idx = 1
            pid = str(row[idx]) if id_field else f"point_{i + 1}"
            if id_field:
                idx += 1
            bearing = float(row[idx] or 0.0) if bearing_field else 0.0
            pts.append({"point_id": pid, "lng": lon, "lat": lat, "bearing": bearing})
    return pts


# ------------------------------------------------------------------ 下载主流程

def download_streetview(
    points: Sequence[dict[str, Any]],
    images_dir: str,
    *,
    mode: str = "4_views",
    level: int = 3,
    crop_bottom: float = 0.4,
    crop_top: float = 0.0,
    search_radius: float = 5.0,
    interval: float = 0.3,
    timeout: int = 30,
    retries: int = 2,
    on_point: Callable[[int, int, str, dict[str, int]], None] | None = None,
) -> list[dict[str, Any]]:
    """逆向接口批量下载街景，返回报告 list[dict]（同时写 download_report.csv）。

    points: list[dict] 含 point_id / lng / lat / bearing（WGS84）。
    mode：4_views / left_right / front_back / panorama。
    on_point(done, total, pid, counters)：点级进度回调，counters 含 ok/skip/no_pano/fail。
    """
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    client = BaiduReverseClient(sleep_seconds=interval, timeout=timeout, max_retries=retries)
    is_pano = mode == "panorama"
    suffixes = ["pano"] if is_pano else [s for _, s in get_headings_for_mode(mode, 0.0)]

    report: list[dict[str, Any]] = []
    counters = {"ok": 0, "skip": 0, "no_pano": 0, "fail": 0}
    total = len(points)

    for i, pt in enumerate(points):
        pid = str(pt["point_id"])
        lat, lon = float(pt["lat"]), float(pt["lng"])
        bearing = float(pt.get("bearing") or 0.0)

        # 断点续跑：该点目标图已齐全则跳过
        if is_pano:
            done = _has_image(images_dir, pid, "pano")
        else:
            done = all(_has_image(images_dir, pid, s) for s in suffixes)
        if done:
            counters["skip"] += 1
            road_name, shot_date = _parse_road_date(images_dir, pid, suffixes)
            rep: dict[str, Any] = {"point_id": pid, "status": "ok", "lat": lat, "lon": lon}
            if road_name:
                rep["road_name"] = road_name
            if shot_date:
                rep["shot_date"] = shot_date
            for s in suffixes:
                p = _find_image(images_dir, pid, s)
                if p:
                    rep[f"img_{s}"] = p
            report.append(rep)
            if on_point:
                on_point(i + 1, total, pid, counters)
            continue

        # WGS84 → BD09MC（仅用于发请求）
        mc_x, mc_y = wgs84_to_bd09mc(lon, lat)
        panoid = None
        nearest_m = None
        err = None
        try:
            panoid = client.get_panoid(mc_x, mc_y)
        except Exception as e:  # noqa: BLE001 - 网络/解析异常统一记录
            err = e

        # 坐标本身无街景时，就近搜索兜底
        if err is None and not panoid and search_radius > 0:
            try:
                panoid, nearest_m = client.find_nearest_panoid(mc_x, mc_y, search_radius)
            except Exception:  # noqa: BLE001
                panoid, nearest_m = None, None

        if err is not None:
            counters["fail"] += 1
            report.append({"point_id": pid, "status": "download_failed",
                           "lat": lat, "lon": lon, "message": type(err).__name__})
        elif not panoid:
            counters["no_pano"] += 1
            report.append({"point_id": pid, "status": "no_pano", "lat": lat, "lon": lon})
        else:
            try:
                car_heading = road_name = shot_date = None
                cam = client.get_camera_info(panoid)
                if cam:
                    if cam.get("Rname"):
                        road_name = str(cam["Rname"])
                    if cam.get("Date"):
                        shot_date = str(cam["Date"])
                    if "Heading" in cam:
                        car_heading = float(cam["Heading"])

                pano = client.download_panorama(panoid, level)
                rep: dict[str, Any] = {"point_id": pid, "status": "ok", "lat": lat, "lon": lon,
                                       "panoid": panoid}
                if is_pano:
                    stem = _img_stem(pid, road_name, shot_date, "pano")
                    crop_streetview(pano, crop_bottom, crop_top).save(
                        images_dir / f"{stem}.jpg", quality=90)
                    rep["img_pano"] = str(images_dir / f"{stem}.jpg")
                else:
                    heading_offset = (90.0 - car_heading) % 360 if car_heading is not None else 0.0
                    for suffix, img in slice_views(pano, bearing, mode, crop_bottom, crop_top,
                                                   heading_offset=heading_offset).items():
                        stem = _img_stem(pid, road_name, shot_date, suffix)
                        img.save(images_dir / f"{stem}.jpg", quality=90)
                        rep[f"img_{suffix}"] = str(images_dir / f"{stem}.jpg")

                counters["ok"] += 1
                if road_name:
                    rep["road_name"] = road_name
                if shot_date:
                    rep["shot_date"] = shot_date
                if car_heading is not None:
                    rep["car_heading"] = round(car_heading, 2)
                if nearest_m is not None:
                    rep["message"] = f"就近匹配 {nearest_m:.0f}m"
                report.append(rep)
            except Exception as e:  # noqa: BLE001 - 单点失败不中断整批
                counters["fail"] += 1
                report.append({"point_id": pid, "status": "download_failed",
                               "lat": lat, "lon": lon, "message": type(e).__name__})

        if on_point:
            on_point(i + 1, total, pid, counters)

    _write_report_csv(images_dir, report)
    return report


def _write_report_csv(images_dir: Path, report: Sequence[dict[str, Any]]) -> None:
    """写 download_report.csv（utf-8-sig，Excel 可直接打开）。"""
    path = images_dir / "download_report.csv"
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for r in report:
                writer.writerow(r)
    except PermissionError:
        print("[warn] download_report.csv 写入失败（文件可能正被 Excel 占用），"
              "街景图片已正常保存")
