"""配额常量、状态码、干跑估算。

从原项目 api/quota.py 移植。百度按「路线数」（起点数 × 终点数）计配额与并发；
腾讯按「计算次数」（每对起终点 1 次）计。干跑估算供工具 2（仅估算不请求）复用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---- 百度批量算路配额 ----
DAILY_QUOTA = 30000              # 路线数/天（以控制台为准）
CONCURRENCY_LIMIT = 50           # 路线数/秒
MAX_ORIGINS_PER_REQUEST = 5
MAX_DESTS_PER_REQUEST = 10
MAX_PAIRS_PER_REQUEST = 50

# ---- 腾讯距离矩阵配额 ----
TENCENT_DAILY_QUOTA = 6000       # 计算次数/天
TENCENT_CONCURRENCY_LIMIT = 5    # 并发计算量/秒
TENCENT_MAX_DESTS_PER_REQUEST = 200  # 一对多单次终点 API 上限（个人并发 5 下实际 ≤5）

# ---- 百度状态码 ----
STATUS_MESSAGES = {
    0: "成功",
    1: "服务器内部错误",
    2: "请求参数非法",
    101: "AK 参数不存在",
    200: "APP 不存在",
    201: "APP 被用户禁用",
    202: "APP 被管理员删除",
    210: "APP SN 校验失败",
    211: "APP 服务被禁用",
    220: "APP 无权限",
    221: "APP 审核未通过",
    302: "天配额超限，限制访问",
    401: "当前并发量已经超过约定并发配额",
}
QUOTA_EXCEEDED_CODES = {302}     # 日配额耗尽：停跑，明日续
RATE_LIMIT_CODES = {401}         # 并发超限：同 AK 退避重试，不轮换
# AK 级错误码：换另一个 AK 才可能成功 → 触发轮换（401 不在此列）
AK_ROTATE_CODES = {101, 200, 201, 202, 210, 211, 220, 221, 302}


class QuotaExhausted(RuntimeError):
    """日配额耗尽：保存现场，次日续跑。"""


class APIError(RuntimeError):
    """请求级错误（非配额、非网络），携带状态码。"""

    def __init__(self, status, message):
        self.status = status
        super().__init__(f"[status={status}] {message}")


@dataclass
class Estimate:
    n_points: int
    n_origins: int
    n_requests: int
    n_pairs: int               # 最终路线数（配额/并发计量口径）
    daily_quota: int
    fits_in_quota: bool
    est_seconds: float
    note: str
    peak_pairs_per_sec: float = 0.0


def estimate_requests(n_points: int, n_origins: int,
                      origins_per_request: int = MAX_ORIGINS_PER_REQUEST,
                      dests_per_request: int = MAX_DESTS_PER_REQUEST) -> int:
    """HTTP 请求次数 = 起点分组数 × 每组终点分批数（只用于耗时估算）。"""
    if n_points <= 0 or n_origins <= 0:
        return 0
    origin_groups = math.ceil(n_origins / origins_per_request)
    dest_groups = math.ceil(n_points / dests_per_request)
    return origin_groups * dest_groups


def dry_run_estimate(
    n_points: int,
    n_origins: int,
    qps: float,
    origins_per_request: int = MAX_ORIGINS_PER_REQUEST,
    dests_per_request: int = MAX_DESTS_PER_REQUEST,
    daily_quota: int = DAILY_QUOTA,
    concurrency_limit: int = CONCURRENCY_LIMIT,
    provider: str = "百度",
) -> Estimate:
    """干跑估算：请求次数、路线数（配额口径）、耗时与并发峰值。"""
    n_req = estimate_requests(n_points, n_origins, origins_per_request, dests_per_request)
    n_pairs = n_points * n_origins
    pairs_per_request = min(n_origins, origins_per_request) * dests_per_request

    peak_req_per_sec = math.floor(qps) + 1 if qps > 0 else 0
    peak_pairs_per_sec = peak_req_per_sec * pairs_per_request
    est_seconds = n_req / qps if qps > 0 else float("inf")

    fits = n_pairs <= daily_quota
    notes = []
    if fits:
        notes.append(f"约占日配额 {n_pairs / daily_quota:.1%}"
                     f"（{n_pairs}/{daily_quota} 条路线，{provider}按路线数而非请求数计配额）")
    else:
        notes.append(f"超出日配额（{n_pairs} 条路线 > {daily_quota}）！"
                     f"建议调大渔网间距或分 {n_pairs // daily_quota + 1} 天续跑完成")

    if qps > 0 and peak_pairs_per_sec > concurrency_limit:
        max_req_per_sec = concurrency_limit / pairs_per_request
        if max_req_per_sec < 1:
            notes.append(f"每请求 {pairs_per_request} 条路线已用满并发上限 "
                         f"{concurrency_limit} 条/秒，降低 qps 也无法避免 401，请减小批量")
        else:
            safe_qps = math.floor(max_req_per_sec - 1) + 0.9
            notes.append(f"并发峰值约 {peak_pairs_per_sec:.0f} 条路线/秒"
                         f"（超过并发上限 {concurrency_limit} 条/秒），会触发 401，"
                         f"建议 qps ≤ {safe_qps:.1f}")

    return Estimate(
        n_points=n_points, n_origins=n_origins, n_requests=n_req, n_pairs=n_pairs,
        daily_quota=daily_quota, fits_in_quota=fits, est_seconds=est_seconds,
        note="；".join(notes), peak_pairs_per_sec=peak_pairs_per_sec,
    )
