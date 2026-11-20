"""高德 Web 服务 API 封装（place/polygon 分页 + key 轮换 + 退避）。

复用 geo_common.keypool / ratelimit，只保留高德特有部分。

错误分类与动作：
- INVALID_USER_KEY 等      → 标记 DEAD，换 key（不计重试次数）
- DAILY_QUERY_OVER_LIMIT   → 标记 COOLDOWN，换 key（不计重试次数）
- CUQPS_HAS_EXCEEDED_THE_LIMIT → 不换 key，指数退避重试，耗尽抛 AmapApiError
- 网络异常 / JSON 解析失败  → 指数退避重试，耗尽抛 AmapApiError
- 其他 status=0            → 不重试，直接抛 AmapApiError
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Iterator

import requests

from geo_common.coords import wgs84_to_gcj02
from geo_common.keypool import AllKeysExhausted, KeyPool
from geo_common.ratelimit import RateLimiter, backoff_delay

logger = logging.getLogger("amap_poi.client")

DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
POLYGON_URL = "https://restapi.amap.com/v3/place/polygon"

KEY_DEAD_INFO = {"INVALID_USER_KEY", "USERKEY_PLAT_NOMATCH", "USER_KEY_RECYCLED"}
KEY_OVER_LIMIT_INFO = {"DAILY_QUERY_OVER_LIMIT"}
QPS_INFO = {"CUQPS_HAS_EXCEEDED_THE_LIMIT"}


class AmapApiError(RuntimeError):
    """非 key 类、非限流类 API 错误，或限流/网络重试耗尽（调度层记失败网格后继续）。"""

    def __init__(self, info: str, infocode: str | None = None):
        self.info = info
        self.infocode = infocode
        super().__init__(f"高德 API 错误: {info}" + (f" (infocode={infocode})" if infocode else ""))


def polygon_param(grid) -> str:
    """网格 → 高德 polygon 参数（请求坐标：GCJ-02）格式：左上|右下，6 位小数。

    内部几何为 WGS-84，发请求前将两个角点转为 GCJ-02（偏移在 0.1° 网格内近线性，
    角点转换 ≈ 整窗平移，误差米级），确保搜索窗与画在地图上的网格重合。
    """
    nw_lng, nw_lat = wgs84_to_gcj02(grid.xmin, grid.ymax)
    se_lng, se_lat = wgs84_to_gcj02(grid.xmax, grid.ymin)
    return f"{nw_lng:.6f},{nw_lat:.6f}|{se_lng:.6f},{se_lat:.6f}"


class AmapClient:
    """高德 API 客户端（单线程，内建限速）。"""

    def __init__(
        self,
        key_pool: KeyPool,
        *,
        qps: float = 2.0,
        max_retries: int = 5,
        retry_base: float = 1.0,
        timeout: float = 10.0,
        sleep_between_pages: float = 1.0,
        session: requests.Session | None = None,
    ):
        self.pool = key_pool
        self.limiter = RateLimiter(qps)
        self.max_retries = max_retries
        self.retry_base = retry_base
        self.timeout = timeout
        self.sleep_between_pages = sleep_between_pages
        self.session = session or requests.Session()
        self.request_count = 0  # 实际发出的 HTTP 请求数（含重试）

    # ------------------------------------------------------------------ 内部

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """带 key 轮换 + 指数退避的 GET，成功返回 JSON dict。"""
        attempt = 0
        while True:
            key = self.pool.current()  # 可能抛 AllKeysExhausted
            try:
                self.limiter.wait()
                resp = self.session.get(url, params={**params, "key": key}, timeout=self.timeout)
                self.request_count += 1
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                if attempt >= self.max_retries:
                    raise AmapApiError(f"网络异常重试 {self.max_retries} 次后仍失败: {e}") from e
                delay = backoff_delay(self.retry_base, attempt)
                logger.warning("请求异常（%s），%.1fs 后第 %d/%d 次重试",
                               e, delay, attempt + 1, self.max_retries)
                time.sleep(delay)
                attempt += 1
                continue

            if str(data.get("status")) == "1":
                return data

            info = str(data.get("info", ""))
            infocode = data.get("infocode")
            if info in KEY_DEAD_INFO:
                self.pool.mark_dead(key)
                logger.error("key %s 无效（%s），已弃用并切换", KeyPool.mask(key), info)
                continue
            if info in KEY_OVER_LIMIT_INFO:
                self.pool.mark_cooldown(key)
                logger.warning("key %s 配额耗尽（%s），已切换", KeyPool.mask(key), info)
                continue
            if info in QPS_INFO:
                if attempt >= self.max_retries:
                    raise AmapApiError(f"QPS 限流重试 {self.max_retries} 次后仍失败", infocode)
                delay = backoff_delay(self.retry_base, attempt)
                logger.warning("QPS 限流，%.1fs 后第 %d/%d 次重试", delay, attempt + 1, self.max_retries)
                time.sleep(delay)
                attempt += 1
                continue
            raise AmapApiError(info, infocode)

    @staticmethod
    def _normalize_types(types: str) -> str:
        """多类型分隔符统一为高德要求的 "|"（允许用户用逗号）。"""
        return "|".join(t.strip() for t in types.replace("|", ",").split(",") if t.strip())

    # ------------------------------------------------------------------ 行政区

    def district(self, keywords: str) -> dict[str, str]:
        """查询行政区，返回 {polyline, adcode, name}。polyline 为 GCJ-02。"""
        data = self._get_json(DISTRICT_URL, {
            "keywords": keywords,
            "extensions": "all",
            "subdistrict": 0,
        })
        districts = data.get("districts") or []
        if not districts:
            raise AmapApiError(f"未找到行政区: {keywords}")
        top = districts[0]
        polyline = top.get("polyline") or ""
        if not polyline:
            raise AmapApiError(f"行政区 {keywords}（{top.get('name')}）无边界数据，可能层级过细")
        return {
            "polyline": polyline,
            "adcode": str(top.get("adcode", "")),
            "name": str(top.get("name", "")),
        }

    # ------------------------------------------------------------------ 多边形搜索

    def polygon_first_page(
        self,
        polygon: str,
        types: str,
        *,
        offset: int = 20,
        extensions: str = "all",
    ) -> tuple[int, list[dict[str, Any]]]:
        """请求第 1 页，同时拿到 count（探测与首页数据一次请求完成）。"""
        data = self._get_json(POLYGON_URL, {
            "types": self._normalize_types(types),
            "polygon": polygon,
            "offset": offset,
            "page": 1,
            "extensions": extensions,
        })
        try:
            count = int(data.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        return count, list(data.get("pois") or [])

    def polygon_iter(
        self,
        polygon: str,
        types: str,
        *,
        offset: int = 20,
        extensions: str = "all",
        count: int,
        first_pois: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        """分页迭代 POI：先吐出第 1 页（复用探测响应），再续翻到末尾。"""
        yield from first_pois
        total_pages = math.ceil(count / offset) if offset > 0 else 0
        for page in range(2, total_pages + 1):
            if self.sleep_between_pages > 0:
                time.sleep(self.sleep_between_pages)
            data = self._get_json(POLYGON_URL, {
                "types": self._normalize_types(types),
                "polygon": polygon,
                "offset": offset,
                "page": page,
                "extensions": extensions,
            })
            pois = list(data.get("pois") or [])
            if not pois:
                logger.debug("第 %d 页返回空，提前终止翻页", page)
                break
            yield from pois
