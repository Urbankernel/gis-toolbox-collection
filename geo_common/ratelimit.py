"""QPS 限流 + 指数退避重试。

- backoff_delay：指数退避等待时长（base × 2^n + 随机抖动，避免多实例同频重试）。
- RateLimiter：简单限速器，保证相邻请求间隔 ≥ 1/qps。
"""

from __future__ import annotations

import random
import time


def backoff_delay(base: float, attempt: int, *, jitter_ratio: float = 0.3) -> float:
    """第 attempt 次重试（从 0 计）的等待秒数。

    参数:
        base: 退避基数（秒）
        attempt: 重试序号，0 表示第一次重试
        jitter_ratio: 抖动幅度比例（0~base*2^attempt*jitter_ratio 的随机量）
    """
    delay = base * (2 ** attempt)
    return delay + random.uniform(0, delay * jitter_ratio)


class RateLimiter:
    """简单限速：保证相邻两次请求间隔 ≥ interval 秒。"""

    def __init__(self, qps: float = 2.0):
        self.qps = qps
        self.interval = 1.0 / qps if qps > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        """按需 sleep，使本次请求满足限速要求。"""
        if self.interval <= 0:
            return
        wait = self.interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
