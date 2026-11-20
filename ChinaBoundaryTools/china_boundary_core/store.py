"""本地缓存：原始响应 JSON 落盘，带 TTL，用于避免重复请求与离线重建。

近原样移植自 Urbankernel/chinaboundary。工具箱中作为可选「缓存目录」参数，
默认关闭（不传 cache_dir 即不启用）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time


class Store:
    """以 URL 为键的简单 JSON 文件缓存。"""

    def __init__(self, cache_dir: str | None, enabled: bool = True, ttl_hours: float = 168.0):
        self.enabled = enabled
        self.cache_dir = cache_dir
        self.ttl = ttl_hours * 3600.0
        if self.enabled and self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{digest}.json")

    def get(self, key: str):
        if not self.enabled or not self.cache_dir:
            return None
        path = self._path(key)
        if not os.path.exists(path):
            return None
        if self.ttl > 0 and time.time() - os.path.getmtime(path) > self.ttl:
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def put(self, key: str, data) -> None:
        if not self.enabled or not self.cache_dir:
            return
        try:
            tmp = self._path(key) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._path(key))
        except Exception:
            # 缓存写入失败不应影响主流程
            pass
