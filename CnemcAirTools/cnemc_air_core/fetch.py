"""采集编排：城市级 + 点位级 实时/历史 → 规范化记录。

流程（沿用原项目 cnemc-air，去掉 YAML Config 依赖，改为参数直传）：
1. 解析城市（城市名或城市编码 → 城市中心点元信息）；
2. 城市级：实时六参数 / 近 24 小时 / 近 14 天日报；
3. 点位级：全部点位实时 / 逐点位近 24 小时；
4. 统一构建规范化记录（WGS-84），原始响应保留在 raw 供审计。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .client import CnemcClient
from .normalize import (
    build_city_day,
    build_city_hour,
    build_city_meta,
    build_city_realtime,
    build_station_hour,
    build_station_realtime,
)

logger = logging.getLogger("cnemc_air.fetch")


def _match_city(cities: list[dict], *, name: str | None = None,
                code: str | None = None) -> dict:
    """在已拉取的城市列表中匹配目标城市（不发起请求）。"""
    if code is not None:
        target = str(code).strip()
        for c in cities:
            if str(c.get("CityCode") or "").strip() == target:
                return build_city_meta(c)
        raise RuntimeError(f"未找到城市编码 {target}")

    if name is not None:
        target = name.strip()
        for c in cities:
            if str(c.get("Area") or "").strip() == target:
                return build_city_meta(c)
        matched = [c for c in cities
                   if target in str(c.get("Area") or "") or str(c.get("Area") or "") in target]
        if len(matched) == 1:
            return build_city_meta(matched[0])
        if matched:
            names = [str(c.get("Area")) for c in matched]
            raise RuntimeError(f"城市名 {target!r} 匹配到多个: {names}，请改用城市编码")
        raise RuntimeError(f"未找到城市 {target!r}")

    raise RuntimeError("必须提供城市名 name 或城市编码 code")


def resolve_city(client: CnemcClient, *, name: str | None = None,
                 code: str | None = None) -> dict:
    """城市名或城市编码 → 城市元信息（拉取全国城市列表后本地匹配）。"""
    cities = client.get_all_cities()
    if not isinstance(cities, list) or not cities:
        raise RuntimeError("城市列表接口返回为空或结构异常，接口可能已变化")
    return _match_city(cities, name=name, code=code)


def resolve_city_by_list(cities: list[dict], *, name: str | None = None,
                         code: str | None = None) -> dict:
    """基于已拉取的城市列表本地匹配（多城市循环复用，避免重复请求）。"""
    return _match_city(cities, name=name, code=code)


def _as_list(obj: Any) -> list[dict]:
    return obj if isinstance(obj, list) else []


def fetch(
    client: CnemcClient,
    meta: dict,
    *,
    fetch_city: bool = True,
    fetch_stations: bool = True,
    fetch_station_history: bool = False,
    sleep_between_stations: float = 1.0,
) -> dict:
    """执行一次完整采集，返回规范化数据集与原始响应。"""
    city_name = meta["city_name"]
    city_code = meta["city_code"]
    logger.info("城市: %s（%s）", city_name, city_code)

    raw: dict[str, Any] = {}
    city_realtime: dict | None = None
    city_hour: list[dict] = []
    city_day: list[dict] = []
    stations: list[dict] = []
    station_hour: list[dict] = []

    # ---- 城市级 ----
    if fetch_city:
        rt = client.get_city_realtime(city_code)
        raw["city_realtime"] = rt
        if isinstance(rt, list) and rt:
            rt = rt[0]
        if isinstance(rt, dict):
            city_realtime = build_city_realtime(rt, meta)
            logger.info("城市实时: AQI %s（%s）",
                        city_realtime["aqi"], city_realtime["quality"])

        ch = client.get_city_hour(city_code)
        raw["city_hour"] = ch
        city_hour = [build_city_hour(x, meta) for x in _as_list(ch) if isinstance(x, dict)]
        city_hour.sort(key=lambda x: x.get("time_point") or "")
        logger.info("城市近24小时: %d 条", len(city_hour))

        cd = client.get_city_day(city_code)
        raw["city_day"] = cd
        city_day = [build_city_day(x, meta) for x in _as_list(cd) if isinstance(x, dict)]
        city_day.sort(key=lambda x: x.get("day") or "")
        logger.info("城市近14天日报: %d 条", len(city_day))

    # ---- 点位级 ----
    if fetch_stations:
        st = client.get_stations(city_name)
        raw["stations"] = st
        stations = [build_station_realtime(x, meta) for x in _as_list(st) if isinstance(x, dict)]
        stations.sort(key=lambda x: (x.get("station_name") or "", x.get("time_point") or ""))
        logger.info("点位实时: %d 个", len(stations))

        if fetch_station_history:
            for s in stations:
                code = s.get("station_code")
                if not code:
                    continue
                rows = client.get_station_hour(code)
                raw[f"station_hour_{code}"] = rows
                for x in _as_list(rows):
                    if not isinstance(x, dict):
                        continue
                    r = build_station_hour(x, meta)
                    if not r["station_code"]:
                        r["station_code"] = code
                    if not r["station_name"]:
                        r["station_name"] = s.get("station_name")
                    if r["lon"] is None:
                        r["lon"], r["lat"] = s.get("lon"), s.get("lat")
                    station_hour.append(r)
                if sleep_between_stations > 0:
                    time.sleep(sleep_between_stations)
            station_hour.sort(key=lambda x: (x.get("station_code") or "", x.get("time_point") or ""))
            logger.info("点位近24小时: %d 条", len(station_hour))

    return {
        "city_meta": meta,
        "city_realtime": city_realtime,
        "city_hour": city_hour,
        "city_day": city_day,
        "stations": stations,
        "station_hour": station_hour,
        "raw": raw,
    }
