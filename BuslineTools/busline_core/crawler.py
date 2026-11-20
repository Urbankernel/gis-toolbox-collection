# -*- coding: utf-8 -*-
"""抓取编排：关键字队列 → 翻页检索 → 解析 → 去重 → 坐标转换。

纯逻辑（不依赖 arcpy / shapely），可命令行 pytest（client 用 mock）。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from geo_common.coords import gcj02_to_wgs84
from geo_common.keypool import AllKeysExhausted

from .client import AmapBusClient
from .parser import amap_parse, dedup_lines, norm_line_key, short_name

ProgressFn = Callable[[int, int, str], None]


def fetch_keyword(client: AmapBusClient, city: str, keyword: str,
                  *, offset: int = 20) -> list[dict[str, Any]]:
    """翻页抓取某关键字的全量线路（返回 busline 原始 dict 列表）。"""
    lines: list[dict[str, Any]] = []
    page = 1
    while True:
        data = client.search(city, keyword, offset=offset, page=page)
        batch = data.get("buslines") or []
        lines.extend(batch)
        try:
            count = int(str(data.get("count", "0") or "0"))
        except (ValueError, TypeError):
            count = 0
        # 已取全 或 本页不满一页 → 停止翻页
        if count and len(lines) >= count:
            break
        if len(batch) < offset:
            break
        page += 1
    return lines


def crawl(client: AmapBusClient, city: str, keywords: list[str], *,
          offset: int = 20, exact: bool = False,
          on_progress: Optional[ProgressFn] = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """遍历关键字队列抓取，返回 (线路记录, 站点记录, 统计)。

    - 采集侧按线路完整名规范化去重；
    - exact=True 时只保留短名与关键字严格全等的线路；
    - 站点/线路坐标 GCJ-02 → WGS84；
    - 输出前按线路 route_name + 站点复合键二次兜底去重。
    AllKeysExhausted 向上抛出（key 全部不可用）；单个关键字失败记录后继续。
    """
    line_rows: list[dict[str, Any]] = []
    stop_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    failed: list[str] = []

    total = len(keywords)
    for i, kw in enumerate(keywords):
        if on_progress:
            on_progress(i, total, kw)
        try:
            lines = fetch_keyword(client, city, kw, offset=offset)
        except AllKeysExhausted:
            raise
        except RuntimeError as exc:
            failed.append(f"{kw}: {exc}")
            continue

        for bl in lines:
            if not isinstance(bl, dict):
                continue
            name = bl.get("name", "")
            key = norm_line_key(name)
            if not name or key in seen:
                continue
            if exact and norm_line_key(short_name(name)) != norm_line_key(kw):
                continue

            line_rec, stop_recs = amap_parse(bl)
            if line_rec is None:
                continue
            seen.add(key)

            # 坐标转换
            line_rec["coord_arr_wgs84"] = [
                gcj02_to_wgs84(*c) for c in line_rec["coord_arr"]]
            line_rec["polyline_wgs84"] = ";".join(
                f"{lng},{lat}" for lng, lat in line_rec["coord_arr_wgs84"])
            for sr in stop_recs:
                if sr["lng"] and sr["lat"]:
                    try:
                        wlng, wlat = gcj02_to_wgs84(float(sr["lng"]), float(sr["lat"]))
                        sr["lng_wgs84"] = f"{wlng:.6f}"
                        sr["lat_wgs84"] = f"{wlat:.6f}"
                    except ValueError:
                        pass

            line_rows.append(line_rec)
            stop_rows.extend(stop_recs)

    line_rows, stop_rows = dedup_lines(line_rows, stop_rows)
    stats = {
        "fetched_lines": len(line_rows),
        "fetched_stops": len(stop_rows),
        "failed": failed,
    }
    return line_rows, stop_rows, stats
