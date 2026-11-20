"""百度批量算路客户端（同步 requests 版）。

从原项目 api/client.py 改造：aiohttp 异步 → requests 同步，
ApiKeyPool → geo_common.keypool.KeyPool（三态），asyncio 限速 → geo_common.ratelimit。

容错语义（与原项目一致，互不混淆）：
- 【轮换】302 配额耗尽 / AK 级错误码（AK_ROTATE_CODES）：标记当前 AK 下线、
  切下一个 AK 重试，不消耗重试次数；全部不可用抛 QuotaExhausted。
- 【重试】401 并发超限 / 网络错误 / 其他 status：指数退避重试 max_retries 次。
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass

import requests

from geo_common.keypool import AllKeysExhausted, KeyPool
from geo_common.ratelimit import RateLimiter, backoff_delay

from .quota import (AK_ROTATE_CODES, APIError, QUOTA_EXCEEDED_CODES,
                    RATE_LIMIT_CODES, STATUS_MESSAGES, QuotaExhausted)

BASE_URL = "https://api.map.baidu.com/routematrix/v2/{trans_type}"


def fmt_coord(lon: float, lat: float) -> str:
    """百度 routematrix 坐标格式：纬度在前。"""
    return f"{lat:.6f},{lon:.6f}"


def build_url(trans_type: str, origins: list[tuple[float, float]],
              dests: list[tuple[float, float]], tactics: str,
              ak: str, coord_type: str = "wgs84") -> str:
    params = {
        "output": "json",
        "origins": "|".join(fmt_coord(lon, lat) for lon, lat in origins),
        "destinations": "|".join(fmt_coord(lon, lat) for lon, lat in dests),
        "coord_type": coord_type,
        "ak": ak,
    }
    if trans_type == "driving":
        params["tactics"] = tactics
    return f"{BASE_URL.format(trans_type=trans_type)}?{urllib.parse.urlencode(params)}"


def parse_matrix(payload: dict, n_origins: int, n_dests: int) -> list[tuple[float | None, float | None]]:
    """解析批量算路响应（扁平 result[]，origin-major）。

    duration/distance 的 value < 0 视为不可达。返回 [(duration_min, distance_km), ...]。
    """
    status = payload.get("status")
    if status != 0:
        message = payload.get("message", STATUS_MESSAGES.get(status, "未知错误"))
        if status in QUOTA_EXCEEDED_CODES:
            raise QuotaExhausted(f"日配额耗尽（status={status}: {message}），已保存现场，可明日续跑")
        raise APIError(status, message)

    result = payload.get("result") or []
    expected = n_origins * n_dests
    if len(result) != expected:
        raise APIError(status, f"返回对数异常：期望 {expected} 对，实际 {len(result)} 对")

    pairs: list[tuple[float | None, float | None]] = []
    for item in result:
        dur = (item.get("duration") or {}).get("value")
        dist = (item.get("distance") or {}).get("value")
        dur_min = round(dur / 60, 1) if isinstance(dur, (int, float)) and dur >= 0 else None
        dist_km = round(dist / 1000, 3) if isinstance(dist, (int, float)) and dist >= 0 else None
        pairs.append((dur_min, dist_km))
    return pairs


@dataclass
class Batch:
    origin_ids: list[int]                 # 全局出发点序号（0 起）
    origins: list[tuple[float, float]]    # (lon, lat)
    oids: list[int]                       # 终点 oid
    dests: list[tuple[float, float]]      # (lon, lat)


def plan_batches(origins: list[tuple[float, float]],
                 pending: list,  # list[GridPoint]
                 origins_per_request: int = 5,
                 dests_per_request: int = 10) -> list[Batch]:
    """起点按 5 个一组、终点按 10 个一组做笛卡尔分批。"""
    batches: list[Batch] = []
    for o_start in range(0, len(origins), origins_per_request):
        o_group = origins[o_start:o_start + origins_per_request]
        o_ids = list(range(o_start, o_start + len(o_group)))
        for d_start in range(0, len(pending), dests_per_request):
            d_group = pending[d_start:d_start + dests_per_request]
            batches.append(Batch(
                origin_ids=o_ids,
                origins=o_group,
                oids=[p.oid for p in d_group],
                dests=[(p.lon, p.lat) for p in d_group],
            ))
    return batches


class RouteMatrixClient:
    """百度批量算路同步客户端：requests 单会话 + 限速 + 退避重试 + 多 AK 轮换。

    aks 接受 str 或 list[str]（分号分隔的多 key 在此拆好再传入）。
    """

    def __init__(self, aks, trans_type: str = "driving", tactics: str = "11",
                 qps: float = 3.0, max_retries: int = 3, timeout: float = 30.0):
        if isinstance(aks, str):
            aks = [aks]
        aks = [k.strip() for k in aks if k and k.strip()]
        self.pool = KeyPool(aks)
        self.trans_type = trans_type
        self.tactics = tactics
        self.max_retries = max_retries
        self.timeout = timeout
        self.limiter = RateLimiter(qps)
        self._session = requests.Session()

    def fetch_batch(self, batch: Batch) -> list[tuple[float | None, float | None]]:
        """请求一个批量，返回 len(origin_ids)×len(oids) 个 (duration_min, distance_km)。"""
        attempt = 0
        while True:
            try:
                ak = self.pool.current()
            except AllKeysExhausted as e:
                raise QuotaExhausted(str(e)) from e

            url = build_url(self.trans_type, batch.origins, batch.dests,
                            self.tactics, ak)
            self.limiter.wait()
            try:
                resp = self._session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
                return parse_matrix(payload, len(batch.origins), len(batch.dests))
            except QuotaExhausted:
                # 302：该 AK 今日配额耗尽 → 轮换；单 AK 时下一轮 current() 即抛停跑
                self.pool.mark_cooldown(ak)
                continue
            except APIError as e:
                if e.status in AK_ROTATE_CODES:
                    # AK 本身有问题（无效/被禁/无权限）→ 轮换，重试无意义
                    self.pool.mark_dead(ak)
                    continue
                # 401 并发超限等：退避重试
                if attempt >= self.max_retries:
                    raise
            except requests.RequestException as e:
                # 网络错误与 AK 无关，不换 AK，只退避重试
                if attempt >= self.max_retries:
                    raise APIError(None, f"网络错误重试耗尽: {e}") from e
            except ValueError as e:
                # JSON 解析失败
                if attempt >= self.max_retries:
                    raise APIError(None, f"响应解析失败: {e}") from e

            attempt += 1
            time.sleep(min(backoff_delay(1.0, attempt - 1), 30))

    def close(self) -> None:
        self._session.close()
