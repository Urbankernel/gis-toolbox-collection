"""中国环境监测总站空气质量发布平台接口封装（原样移植自 cnemc-air）。

统一根地址 https://air.cnemc.cn:18007。站点为「页面片段 + jQuery AJAX + JSON 接口」结构，
前端脚本直接暴露这些 JSON 接口，无需登录、Cookie、签名或验证码。

关键约定：
- 除 GetAQIHistoryByConditionHis（表单 body `date=...`）外，其余 POST 接口均为
  query 参数 + 空 body；
- HTTP 200 也可能是 HTML 错误页，必须校验 Content-Type 为 JSON；
- 部分「按时间查询」接口在超出窗口时会静默回退到最新数据，调用方需按返回时间校验；
- 默认 TLS 证书校验开启。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .retry import backoff_delay

logger = logging.getLogger("cnemc_air.client")

BASE_URL = "https://air.cnemc.cn:18007"

# 接口路径
P_ALL_CITY_REALTIME = "/CityData/GetAllCityRealTimeAQIModels"
P_ALL_CITY_DAY = "/CityData/GetAllCityDayAQIModels"
P_PROVINCES = "/CityData/GetProvince"
P_CITIES_BY_PID = "/CityData/GetCitiesByPid"
P_CITY_REALTIME = "/CityData/GetAQIDataPublishLiveInfo"
P_STATIONS = "/CityData/GetAQIDataPublishLive"
P_CITY_HOUR = "/HourChangesPublish/GetCityRealTimeAqiHistoryByCondition"
P_CITY_DAY = "/HourChangesPublish/GetCityDayAqiHistoryByCondition"
P_STATION_HOUR = "/HourChangesPublish/GetAqiHistoryByCondition"
P_STATION_IAQI = "/HourChangesPublish/GetIAqiHistoryByCondition"
P_HOUR_SNAPSHOT = "/HourChangesPublish/GetAQIHistoryByConditionHis"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 cnemc-air/0.1 air-quality-research",
    "Referer": BASE_URL + "/",
}


class CnemcApiError(RuntimeError):
    """接口错误（访问拒绝、非 JSON 响应、重试耗尽等）。"""


class CnemcClient:
    """CNEMC 客户端（单线程，内建限速 + 指数退避重试）。"""

    def __init__(
        self,
        *,
        qps: float = 1.0,
        max_retries: int = 5,
        retry_base: float = 1.0,
        timeout: float = 30.0,
        verify: bool = True,
        session: requests.Session | None = None,
    ):
        self.qps = qps
        self.max_retries = max_retries
        self.retry_base = retry_base
        self.timeout = timeout
        self.verify = verify
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.request_count = 0  # 实际发出的 HTTP 请求数（含重试）
        self._last_request_ts = 0.0

    # ------------------------------------------------------------------ 内部
    def _throttle(self) -> None:
        """简单限速：保证相邻请求间隔 ≥ 1/qps。"""
        if self.qps <= 0:
            return
        interval = 1.0 / self.qps
        wait = interval - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _retry_or_raise(self, attempt: int, reason: str) -> None:
        if attempt >= self.max_retries:
            raise CnemcApiError(f"{reason}：重试 {self.max_retries} 次后仍失败")
        delay = backoff_delay(self.retry_base, attempt)
        logger.warning("%s，%.1fs 后第 %d/%d 次重试",
                       reason, delay, attempt + 1, self.max_retries)
        time.sleep(delay)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """带限速 + 指数退避的请求，成功返回解析后的 JSON（list 或 dict）。"""
        url = BASE_URL + path
        attempt = 0
        while True:
            try:
                self._throttle()
                resp = self.session.request(
                    method, url, params=params, data=data,
                    timeout=self.timeout, verify=self.verify,
                )
                self.request_count += 1
            except requests.RequestException as e:
                self._retry_or_raise(attempt, f"请求异常（{e}）")
                attempt += 1
                continue

            if resp.status_code in (401, 403):
                raise CnemcApiError(
                    f"访问被拒绝（HTTP {resp.status_code}），请确认访问授权，不要绕过访问控制")
            if resp.status_code == 429 or resp.status_code >= 500:
                self._retry_or_raise(attempt, f"HTTP {resp.status_code}")
                attempt += 1
                continue

            ctype = resp.headers.get("Content-Type", "")
            if "application/json" not in ctype:
                raise CnemcApiError(
                    f"响应非 JSON（Content-Type={ctype!r}，HTTP {resp.status_code}），"
                    f"可能是错误页或访问控制页")
            try:
                return resp.json()
            except ValueError as e:
                self._retry_or_raise(attempt, f"JSON 解析失败（{e}）")
                attempt += 1
                continue

    # ------------------------------------------------------------------ 城市与点位接口
    def get_all_cities(self) -> Any:
        """全国城市实时 AQI 列表（含城市编码与中心点经纬度）。"""
        return self._request("GET", P_ALL_CITY_REALTIME)

    def get_all_city_day(self) -> Any:
        """全国城市日报 AQI 列表。"""
        return self._request("GET", P_ALL_CITY_DAY)

    def get_provinces(self) -> Any:
        """省份列表（Id / ProvinceCode / ProvinceName）。"""
        return self._request("GET", P_PROVINCES)

    def get_cities_by_pid(self, pid) -> Any:
        """省内城市列表。pid 是省份记录的内部 Id，不是 ProvinceCode。"""
        return self._request("GET", P_CITIES_BY_PID, params={"pid": pid})

    def get_city_realtime(self, city_code) -> Any:
        """指定城市实时六参数（单个 JSON 对象）。"""
        return self._request("POST", P_CITY_REALTIME, params={"cityCode": city_code})

    def get_stations(self, city_name) -> Any:
        """指定城市全部点位实时数据（含点位经纬度）。"""
        return self._request("GET", P_STATIONS, params={"cityName": city_name})

    def get_city_hour(self, city_code) -> Any:
        """指定城市近 24 小时数据（实测固定返回 24 条）。"""
        return self._request("POST", P_CITY_HOUR, params={"citycode": city_code})

    def get_city_day(self, city_code) -> Any:
        """指定城市近 14 天日报（实测固定返回 14 条）。"""
        return self._request("POST", P_CITY_DAY, params={"citycode": city_code})

    def get_station_hour(self, station_code) -> Any:
        """指定点位近 24 小时数据。"""
        return self._request("POST", P_STATION_HOUR, params={"stationCode": station_code})

    def get_station_iaqi(self, station_code) -> Any:
        """指定点位近 24 小时分指数。"""
        return self._request("POST", P_STATION_IAQI, params={"stationCode": station_code})

    def get_hour_snapshot(self, date_str) -> Any:
        """指定小时全国点位快照（表单 body date=...，仅近 24 小时窗口有效）。"""
        return self._request("POST", P_HOUR_SNAPSHOT, data={"date": date_str})
