"""DataV.GeoAtlas 客户端封装 + 指数退避重试 + 可选本地缓存。

端点契约（实测）：
- bound/{adcode}.json        该区域自身边界（1 个 Feature）
- bound/{adcode}_full.json   该区域的直接子级（N 个 Feature）

近原样移植自 Urbankernel/chinaboundary，仅保留 store 可选缓存参数（默认关闭）。
"""
from __future__ import annotations

import logging
import random
import time

import requests

logger = logging.getLogger("chinaboundary.client")

BASE_URL = "https://geo.datav.aliyun.com/areas_v3"
DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_BASE = 1.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GeoAtlasError(RuntimeError):
    """GeoAtlas 请求失败。"""


class GeoAtlasClient:
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_base: float = DEFAULT_RETRY_BASE,
        session: requests.Session | None = None,
        store=None,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base = retry_base
        self.session = session or requests.Session()
        self.store = store

    def self_boundary(self, adcode) -> dict:
        """bound/{adcode}.json —— 该区域自身边界。"""
        return self._cached(f"{BASE_URL}/bound/{adcode}.json")

    def children(self, adcode) -> dict:
        """bound/{adcode}_full.json —— 直接子级；无子级返回空 FeatureCollection。"""
        url = f"{BASE_URL}/bound/{adcode}_full.json"
        data = self._cached(url, allow_missing=True)
        if data is None:
            data = {"type": "FeatureCollection", "features": []}
            if self.store:
                self.store.put(url, data)
        return data

    def _cached(self, url: str, allow_missing: bool = False):
        if self.store:
            cached = self.store.get(url)
            if cached is not None:
                return cached
        data = self._get_json(url, allow_missing=allow_missing)
        if data is not None and self.store:
            self.store.put(url, data)
        return data

    def _get_json(self, url: str, allow_missing: bool = False):
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    text = resp.text.strip()
                    if not text:
                        if allow_missing:
                            return None
                        raise GeoAtlasError(f"空响应: {url}")
                    return resp.json()
                if resp.status_code == 404 and allow_missing:
                    return None
                raise GeoAtlasError(f"HTTP {resp.status_code}: {url}")
            except (requests.RequestException, GeoAtlasError) as e:
                last_err = e
                if attempt >= self.max_retries:
                    break
                wait = self.retry_base * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning("请求失败，%.1fs 后重试(%d/%d)：%s", wait, attempt + 1, self.max_retries, e)
                time.sleep(wait)
        raise GeoAtlasError(f"请求失败: {url} -> {last_err}")
