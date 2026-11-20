"""字段规范化：时间/数值解析、缺失值处理、AQI 等级、规范化记录构建。

纪律（沿用原项目 cnemc-air）：
- 指标值在接口里多为字符串，统一转为可空 float；缺失/描述性文本 → None；
- 时间统一解析为 Asia/Shanghai，输出可读字符串 "YYYY-MM-DD HH:MM:SS"（可字典序排序）；
- 成果坐标统一 WGS-84（lon/lat），不生成 GCJ-02 派生列；
- 原始接口响应完整保存在 raw/ 目录，规范化输出只保留清洗后的值。
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc

# 视为缺失的字符串（大小写不敏感比较用 lower()）
MISSING_VALUES = {"", "na", "n/a", "/", "—", "-", "null", "none", "无", "无效"}

# 源字段 → 规范化字段名（小时值）
_HOUR_POLLUTANTS = {
    "PM2_5": "pm25", "PM10": "pm10", "SO2": "so2", "NO2": "no2",
    "CO": "co", "O3": "o3", "O3_8h": "o3_8h",
}
# 源字段 → 规范化字段名（日均/24h 值，城市日报口径）
_DAY_POLLUTANTS = {
    "PM2_5_24h": "pm25_24h", "PM10_24h": "pm10_24h", "SO2_24h": "so2_24h",
    "NO2_24h": "no2_24h", "CO_24h": "co_24h", "O3_8h_24h": "o3_8h_24h",
}
# 点位级 24h 字段（点位实时/24h 接口额外返回 O3_24h）
_STATION_24H_POLLUTANTS = {
    "PM2_5_24h": "pm25_24h", "PM10_24h": "pm10_24h", "SO2_24h": "so2_24h",
    "NO2_24h": "no2_24h", "CO_24h": "co_24h", "O3_24h": "o3_24h",
    "O3_8h_24h": "o3_8h_24h",
}
# NO / NOx（仅点位实时接口返回，点位 24h 接口无此字段 → None）
_NO_POLLUTANTS = {
    "NO": "no", "NO_24h": "no_24h", "NOx": "nox", "NOx_24h": "nox_24h",
}
# 各污染物 IAQI 分指数等级（int，对应优/良/轻度…等级序号）
_LEVELS = {
    "COLevel": "co_level", "NO2Level": "no2_level", "O3Level": "o3_level",
    "O3_8hLevel": "o3_8h_level", "PM10Level": "pm10_level",
    "PM2_5Level": "pm2_5_level", "SO2Level": "so2_level",
}

# 空气质量指数等级（HJ 633-2012）：下限, 上限, 等级, 配色
AQI_LEVELS = [
    (0, 50, "优", "#00E400"),
    (51, 100, "良", "#FFFF00"),
    (101, 150, "轻度污染", "#FF7E00"),
    (151, 200, "中度污染", "#FF0000"),
    (201, 300, "重度污染", "#8F3F97"),
    (301, 500, "严重污染", "#7E0023"),
]
AQI_NO_DATA_COLOR = "#9E9E9E"


# --------------------------------------------------------------------------- #
# 基础解析
# --------------------------------------------------------------------------- #
def parse_time(value) -> datetime | None:
    """解析接口时间，返回 Asia/Shanghai 时区的 aware datetime；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC).astimezone(SHANGHAI)
    s = str(value).strip()
    if not s:
        return None
    if s.startswith("/Date(") and s.endswith(")/"):
        inner = s[len("/Date("):-len(")/")]
        try:
            ms = int(inner)
        except ValueError:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(SHANGHAI)
    s2 = s
    if s2.endswith("Z"):
        s2 = s2[:-1] + "+00:00"
    if "T" not in s2 and " " in s2:
        s2 = s2.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s2)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    else:
        dt = dt.astimezone(SHANGHAI)
    return dt


def fmt_time(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fmt_date(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d")


def sort_key(dt: datetime | None) -> str:
    return dt.isoformat() if dt is not None else ""


def to_float(value) -> float | None:
    """转可空 float：缺失值 / 描述性文本 → None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    if s.lower() in MISSING_VALUES:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_text(value) -> str | None:
    """转可空文本：None / 缺失占位符 → None，其余去首尾空白。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in MISSING_VALUES:
        return None
    return s


def aqi_level(aqi: float | None) -> tuple[str, str]:
    """AQI → (等级名, 配色)。无数据时返回 ('无数据', 灰)。"""
    if aqi is None:
        return "无数据", AQI_NO_DATA_COLOR
    for lo, hi, label, color in AQI_LEVELS:
        if lo <= aqi <= hi:
            return label, color
    if aqi > 500:
        return "严重污染", "#7E0023"
    return "无数据", AQI_NO_DATA_COLOR


# --------------------------------------------------------------------------- #
# 规范化记录构建（定义成果 schema）
# --------------------------------------------------------------------------- #
def _coords(lat_val, lon_val) -> tuple[float | None, ...]:
    lon = to_float(lon_val)
    lat = to_float(lat_val)
    if lon is not None and lat is not None:
        return round(lon, 6), round(lat, 6)
    return lon, lat


def _pollutants(row: dict, mapping: dict[str, str]) -> dict[str, float | None]:
    return {canonical: to_float(row.get(source)) for source, canonical in mapping.items()}


def _levels(row: dict) -> dict[str, float | None]:
    return {canonical: to_float(row.get(source)) for source, canonical in _LEVELS.items()}


def _base(meta: dict, row: dict) -> dict:
    base = {
        "city_code": meta["city_code"],
        "city_name": meta["city_name"],
        "lon": meta["lon"], "lat": meta["lat"],
        "quality": to_text(row.get("Quality")),
        "primary_pollutant": to_text(row.get("PrimaryPollutant")),
        "measure": to_text(row.get("Measure")),
        "unhealthful": to_text(row.get("Unheathful")),
        "aqi": to_float(row.get("AQI")),
    }
    return base


def build_city_meta(city_model: dict) -> dict:
    """城市列表记录 → 城市元信息（编码/名称/中心点坐标）。"""
    lon, lat = _coords(city_model.get("Latitude"), city_model.get("Longitude"))
    return {
        "city_code": to_text(city_model.get("CityCode")),
        "city_name": to_text(city_model.get("Area")),
        "lon": lon, "lat": lat,
    }


def build_city_realtime(rt: dict, meta: dict) -> dict:
    row = _base(meta, rt)
    row["time_point"] = fmt_time(parse_time(rt.get("TimePoint")))
    row.update(_pollutants(rt, _HOUR_POLLUTANTS))
    row.update(_levels(rt))
    return row


def build_city_hour(it: dict, meta: dict) -> dict:
    row = _base(meta, it)
    row["time_point"] = fmt_time(parse_time(it.get("TimePoint")))
    row["time_point_str"] = to_text(it.get("TimePointStr"))
    row.update(_pollutants(it, _HOUR_POLLUTANTS))
    return row


def build_city_day(it: dict, meta: dict) -> dict:
    row = _base(meta, it)
    row["day"] = fmt_date(parse_time(it.get("TimePoint")))
    row["day_str"] = to_text(it.get("TimePointStr"))
    row.update(_pollutants(it, _DAY_POLLUTANTS))
    return row


def _station_base(row: dict, city_meta: dict) -> dict:
    lon, lat = _coords(row.get("Latitude"), row.get("Longitude"))
    return {
        "station_code": to_text(row.get("StationCode")),
        "station_name": to_text(row.get("PositionName")),
        "city_code": city_meta["city_code"],
        "city_name": city_meta["city_name"],
        "lon": lon, "lat": lat,
        "quality": to_text(row.get("Quality")),
        "primary_pollutant": to_text(row.get("PrimaryPollutant")),
        "measure": to_text(row.get("Measure")),
        "unhealthful": to_text(row.get("Unheathful")),
        "aqi": to_float(row.get("AQI")),
    }


def build_station_realtime(st: dict, city_meta: dict) -> dict:
    row = _station_base(st, city_meta)
    row["time_point"] = fmt_time(parse_time(st.get("TimePoint")))
    row.update(_pollutants(st, _HOUR_POLLUTANTS))
    row.update(_pollutants(st, _STATION_24H_POLLUTANTS))
    row.update(_pollutants(st, _NO_POLLUTANTS))
    row.update(_levels(st))
    return row


def build_station_hour(it: dict, city_meta: dict) -> dict:
    row = _station_base(it, city_meta)
    row["time_point"] = fmt_time(parse_time(it.get("TimePoint")))
    row["time_point_str"] = to_text(it.get("TimePointStr"))
    row.update(_pollutants(it, _HOUR_POLLUTANTS))
    row.update(_pollutants(it, _STATION_24H_POLLUTANTS))
    row.update(_pollutants(it, _NO_POLLUTANTS))
    row.update(_levels(it))
    return row
