"""腾讯距离矩阵批量算路客户端（同步 requests 版）。

从原项目 api/tencent.py 改造：aiohttp → requests 同步，ApiKeyPool → KeyPool。

与百度路径的差异：
- 请求坐标 GCJ-02（WGS-84 先转 GCJ-02）；
- 返回 rows/elements 二层结构（百度为扁平 result[]）；
- 一对多单次最多 200 终点（个人并发 5 下实际 ≤5）。
"""

from __future__ import annotations

import time
import urllib.parse

import requests

from geo_common.keypool import AllKeysExhausted, KeyPool
from geo_common.ratelimit import RateLimiter, backoff_delay

from .client import Batch
from .coords import wgs84_to_gcj02
from .quota import APIError, QuotaExhausted

BASE_URL = "https://apis.map.qq.com/ws/distance/v1/matrix"

STATUS_MESSAGES = {
    0: "成功",
    110: "请求来源未被授权",
    306: "请求签名/护持信息错误",
    310: "请求参数信息有误",
    311: "Key 格式错误",
    1023: "配额/并发超限，限制访问",
}

QUOTA_EXCEEDED_CODES = {1023}          # 配额/并发超限：停跑，明日续
KEY_ROTATE_CODES = {1023, 110, 306, 311}  # key 级错误：换 key 才可能成功
ELEMENT_NO_ROAD = 4                    # 单个起终点对：附近无路（距离为直线、无 duration）

# 出行方式映射：项目 trans_type（百度命名）→ 腾讯 mode
TRANS_MODE = {
    "driving": "driving",
    "walking": "walking",
    "riding": "bicycling",       # 百度习惯叫法 → 自行车骑行
    "bicycling": "bicycling",
    "ebicycling": "ebicycling",
}


def fmt_gcj02(lon: float, lat: float) -> str:
    """WGS84 → GCJ-02 → 腾讯坐标串 "lat,lng"（lat 在前，逗号分隔）。"""
    glon, glat = wgs84_to_gcj02(lon, lat)
    return f"{glat:.6f},{glon:.6f}"


def build_url(key: str, mode: str,
              froms: list[tuple[float, float]],
              tos: list[tuple[float, float]]) -> str:
    params = {
        "key": key,
        "mode": mode,
        "from": ";".join(fmt_gcj02(lon, lat) for lon, lat in froms),
        "to": ";".join(fmt_gcj02(lon, lat) for lon, lat in tos),
    }
    return f"{BASE_URL}?{urllib.parse.urlencode(params)}"


def parse_matrix(payload: dict, n_from: int, n_to: int) -> list[tuple[float | None, float | None]]:
    """解析距离矩阵响应（rows/elements 二层，origin-major）。

    element.status == 4（附近无路）视为不可达。返回 [(duration_min, distance_km), ...]。
    """
    status = payload.get("status")
    if status != 0:
        message = payload.get("message", STATUS_MESSAGES.get(status, "未知错误"))
        if status in QUOTA_EXCEEDED_CODES:
            raise QuotaExhausted(
                f"日配额/并发超限（status={status}: {message}），已保存现场，可明日续跑")
        raise APIError(status, message)

    result = payload.get("result") or {}
    rows = result.get("rows") or []
    if len(rows) != n_from:
        raise APIError(status, f"返回行数异常：期望 {n_from} 行，实际 {len(rows)} 行")

    pairs: list[tuple[float | None, float | None]] = []
    for row in rows:
        elements = row.get("elements") or []
        if len(elements) != n_to:
            raise APIError(status, f"返回列数异常：期望 {n_to} 列，实际 {len(elements)} 列")
        for el in elements:
            if el.get("status") == ELEMENT_NO_ROAD:
                pairs.append((None, None))
                continue
            dist = el.get("distance")
            dur = el.get("duration")
            dur_min = round(dur / 60, 1) if isinstance(dur, (int, float)) and dur >= 0 else None
            dist_km = round(dist / 1000, 3) if isinstance(dist, (int, float)) and dist >= 0 else None
            pairs.append((dur_min, dist_km))
    return pairs


def plan_batches_tencent(origins: list[tuple[float, float]],
                         pending: list,  # list[GridPoint]
                         dests_per_request: int = 5) -> list[Batch]:
    """腾讯一对多打包：每个起点独立，终点按 dests_per_request 分包。

    腾讯多对多受单侧 ≤50 限制，一对多 1×N 是单次覆盖终点最多的方式；
    个人认证并发上限 5 → 默认 1×5。
    """
    batches: list[Batch] = []
    for oi, origin in enumerate(origins):
        for d_start in range(0, len(pending), dests_per_request):
            d_group = pending[d_start:d_start + dests_per_request]
            batches.append(Batch(
                origin_ids=[oi],
                origins=[origin],
                oids=[p.oid for p in d_group],
                dests=[(p.lon, p.lat) for p in d_group],
            ))
    return batches


class TencentMatrixClient:
    """腾讯距离矩阵同步客户端：多 key 轮换 + requests 会话 + 限速 + 退避重试。

    keys 接受 str 或 list[str]。
    """

    def __init__(self, keys, mode: str = "driving",
                 qps: float = 1.0, max_retries: int = 3, timeout: float = 30.0):
        if isinstance(keys, str):
            keys = [keys]
        keys = [k.strip() for k in keys if k and k.strip()]
        self.pool = KeyPool(keys)
        self.mode = mode
        self.max_retries = max_retries
        self.timeout = timeout
        self.limiter = RateLimiter(qps)
        self._session = requests.Session()

    def fetch(self, origins, dests) -> list[tuple[float | None, float | None]]:
        """请求一个起点组 × 终点组，返回 len(origins)×len(dests) 个
        (duration_min, distance_km)，origin-major 扁平。"""
        attempt = 0
        while True:
            try:
                key = self.pool.current()
            except AllKeysExhausted as e:
                raise QuotaExhausted(str(e)) from e

            url = build_url(key, self.mode, origins, dests)
            self.limiter.wait()
            try:
                resp = self._session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                payload = resp.json()
                return parse_matrix(payload, len(origins), len(dests))
            except QuotaExhausted:
                # 1023：该 key 今日配额/并发用尽 → 轮换
                self.pool.mark_cooldown(key)
                continue
            except APIError as e:
                if e.status in KEY_ROTATE_CODES:
                    # key 本身有问题（无权限/签名错/格式错）→ 轮换
                    self.pool.mark_dead(key)
                    continue
                if attempt >= self.max_retries:
                    raise
            except requests.RequestException as e:
                if attempt >= self.max_retries:
                    raise APIError(None, f"网络错误重试耗尽: {e}") from e
            except ValueError as e:
                if attempt >= self.max_retries:
                    raise APIError(None, f"响应解析失败: {e}") from e

            attempt += 1
            time.sleep(min(backoff_delay(1.0, attempt - 1), 30))

    def close(self) -> None:
        self._session.close()
