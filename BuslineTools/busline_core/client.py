# -*- coding: utf-8 -*-
"""高德公交线路 API 客户端：多 key 轮换 + QPS 限速 + key 失效分类处理。

复用 geo_common.keypool.KeyPool（三态轮换）与 ratelimit.RateLimiter / backoff_delay。
requests 由 arcgispro-py3 自带。
"""
from __future__ import annotations

import time
from typing import Any

from geo_common.keypool import AllKeysExhausted, KeyPool
from geo_common.ratelimit import RateLimiter, backoff_delay

API_URL = "https://restapi.amap.com/v3/bus/linename"

# 高德错误码分类（与 KeyPool 三态对应）
_ERR_KEY_INVALID = {"10001", "10002", "10009", "10010", "10011", "10012", "10013", "10014"}
_ERR_QUOTA_EXHAUSTED = {"10003"}
_ERR_QPS_LIMIT = {"10004"}


class AmapBusClient:
    """按关键字请求高德公交线路接口，自动轮换 key 与限速。"""

    def __init__(self, pool: KeyPool, *, qps: float = 28.0,
                 timeout: float = 15.0, retries: int = 3, delay: float = 0.0):
        self.pool = pool
        self.limiter = RateLimiter(qps)
        self.timeout = timeout
        self.retries = retries
        self.delay = delay

    def search(self, city: str, keyword: str, *, offset: int = 20, page: int = 1) -> dict[str, Any]:
        """请求一页，返回解析后的 JSON dict；按错误码标记/切换 key 并重试。

        失败重试耗尽后抛 RuntimeError；key 全部不可用抛 AllKeysExhausted（由调用方处理）。
        """
        import requests

        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            key = self.pool.current()  # 全部不可用会抛 AllKeysExhausted
            self.limiter.wait()
            if self.delay > 0:
                time.sleep(self.delay)
            params = {
                "key": key, "city": city, "keywords": keyword,
                "extensions": "all", "offset": offset, "page": page,
                "output": "json",
            }
            try:
                resp = requests.get(API_URL, params=params, timeout=self.timeout)
                data = resp.json()
            except Exception as exc:  # 网络/解析异常 → 退避重试
                last_err = exc
                time.sleep(backoff_delay(1.0, attempt))
                continue

            status = str(data.get("status", ""))
            if status == "1":
                return data

            infocode = str(data.get("infocode", ""))
            info = str(data.get("info", ""))
            if infocode in _ERR_KEY_INVALID:
                self.pool.mark_dead(key)
                last_err = RuntimeError(f"高德 key 无效({infocode}): {info}")
            elif infocode in _ERR_QUOTA_EXHAUSTED:
                self.pool.mark_cooldown(key)
                last_err = RuntimeError(f"高德 key 日配额耗尽({infocode}): {info}")
            elif infocode in _ERR_QPS_LIMIT:
                time.sleep(1.0)  # 临时限流，等待后重试（不停用 key）
                last_err = RuntimeError(f"高德 QPS 限流({infocode}): {info}")
            else:
                last_err = RuntimeError(f"高德返回错误({infocode}): {info}")

        raise last_err if last_err else RuntimeError(f"请求失败：{keyword}")
