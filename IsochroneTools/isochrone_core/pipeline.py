"""等时圈主流程编排。

从出发点/研究区读几何 → 渔网终点 → 批量算路（断点续跑）→ IDW 插值 →
等值带提取 → 矢量/栅格落盘。

多出发点按「逐像元取最小值」合并（原项目 merge_isochrones 语义），输出
一张合并等时带 + 一张合并时间栅格，单出发点等价于原样。
"""

from __future__ import annotations

from .cache import ResultStore
from .grid import make_fishnet
from .interpolate import (extract_contours, idw_interpolate, merge_time_grids,
                          split_reachable)
from .quota import QuotaExhausted
from .study_area import read_origins, read_polygon
from .writer import (write_fishnet_points, write_isochrones, write_raster,
                     write_unreachable)


def _make_client(engine, aks, trans_type, tactics, qps, max_retries):
    """按引擎创建算路客户端。"""
    if engine == "tencent":
        from .tencent import TRANS_MODE, TencentMatrixClient
        return TencentMatrixClient(aks, mode=TRANS_MODE.get(trans_type, trans_type),
                                   qps=qps, max_retries=max_retries)
    from .client import RouteMatrixClient
    return RouteMatrixClient(aks, trans_type=trans_type, tactics=tactics,
                             qps=qps, max_retries=max_retries)


def _plan_batches(engine, origin, pending, dests_per_request):
    from .client import plan_batches
    from .tencent import plan_batches_tencent
    if engine == "tencent":
        return plan_batches_tencent([origin], pending, dests_per_request=dests_per_request)
    return plan_batches([origin], pending, origins_per_request=1,
                        dests_per_request=dests_per_request)


def _fetch_all(origins, points, engine, trans_type, tactics, aks, qps,
               max_retries, store, messages=None):
    """批量算路，返回 {origin_id: [duration_min 或 None, ...]}（与 points 同序）。"""
    done = store.done_keys() if store else set()

    # 从 store 恢复已有结果
    per_origin_values: dict[int, list] = {}
    for origin_id in range(len(origins)):
        if store:
            per_origin_values[origin_id] = [store.get(origin_id, p.oid)[0] for p in points]
        else:
            per_origin_values[origin_id] = [None] * len(points)

    client = _make_client(engine, aks, trans_type, tactics, qps, max_retries)
    try:
        total = len(origins) * len(points)
        done_count = 0
        for origin_id, origin in enumerate(origins):
            pending = [p for p in points if (origin_id, p.oid) not in done]
            if not pending:
                done_count += len(points)
                continue

            if engine == "tencent":
                dests_per_request = 5
            else:
                dests_per_request = 10
            batches = _plan_batches(engine, origin, pending, dests_per_request)

            for batch in batches:
                try:
                    if engine == "tencent":
                        pairs = client.fetch(batch.origins, batch.dests)
                    else:
                        pairs = client.fetch_batch(batch)
                except QuotaExhausted:
                    if messages:
                        messages.AddWarningMessage(
                            f"出发点 {origin_id} 算路因配额耗尽暂停，已落盘数据不丢，可续跑")
                    raise  # 停跑：已落盘数据已由 store.record 保存
                for j, oid in enumerate(batch.oids):
                    dur, dist = pairs[j]
                    per_origin_values[origin_id][oid - 1] = dur
                    if store:
                        store.record(origin_id, oid, dur, dist)
                done_count += len(batch.oids)
                if messages and total:
                    messages.AddMessage(f"算路进度 {done_count}/{total} 点对")
    finally:
        client.close()

    return per_origin_values


def run_isochrone(
    origins_fc: str,
    area_fc: str,
    engine: str,
    trans_type: str,
    tactics: str,
    thresholds: list[float],
    cell_size: float,
    raster_cell: float,
    aks,
    qps: float,
    max_retries: int,
    out_isochrone: str,
    out_raster: str,
    out_fishnet: str | None = None,
    out_unreachable: str | None = None,
    work_dir: str | None = None,
    idw_power: float = 2.0,
    idw_k: int = 12,
    idw_expand: float = 200.0,
    messages=None,
) -> dict:
    """执行完整等时圈流水线，返回统计 dict。

    aks 为 str（分号分隔多 key）或 list[str]。
    work_dir 非空时启用断点续跑（results.csv 落盘于此）。
    """
    import os

    origins = read_origins(origins_fc)
    polygon = read_polygon(area_fc)

    if messages:
        messages.AddMessage(f"出发点 {len(origins)} 个，研究区范围 {polygon.area:.4f} 平方度")

    # 1. 渔网终点
    points = make_fishnet(polygon, cell_size)
    if not points:
        raise ValueError("研究区范围内未生成任何渔网终点，请检查研究区与渔网间距")
    if messages:
        messages.AddMessage(f"渔网终点 {len(points)} 个")

    # 2. 批量算路（断点续跑）
    store = None
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)
        store = ResultStore(os.path.join(work_dir, "results.csv"))

    if isinstance(aks, str):
        aks = [k.strip() for k in aks.split(";") if k.strip()]

    per_origin_values = _fetch_all(origins, points, engine, trans_type, tactics,
                                   aks, qps, max_retries, store, messages)

    # 3. IDW 插值（每出发点）+ 合并
    grids = []
    for origin_id in range(len(origins)):
        rx, ry, rv, _ = split_reachable(points, per_origin_values[origin_id])
        if len(rx) < 3:
            if messages:
                messages.AddWarningMessage(
                    f"出发点 {origin_id} 有效采样点不足（{len(rx)} < 3），跳过该出发点插值")
            continue
        masked, gt = idw_interpolate(rx, ry, rv, polygon, cell_size=raster_cell,
                                     power=idw_power, k=idw_k, expand=idw_expand)
        grids.append(masked)

    if not grids:
        raise ValueError("所有出发点有效采样点均不足，无法插值")

    if len(grids) == 1:
        merged = grids[0]
    else:
        merged = merge_time_grids(grids)

    # 4. 等值带提取
    bands = extract_contours(merged, gt, thresholds, clip_polygon=polygon, keep_over=True)
    if not bands:
        raise ValueError("等值带提取结果为空，请检查阈值或采样数据")

    # 5. 写成果（合并后单图层：等时带 "isochrone"、不可达点 "unreachable"）
    write_isochrones(out_isochrone, bands, origin_id=0, multi_origin=False)
    if out_raster:
        write_raster(out_raster, merged, gt)
    if out_fishnet:
        write_fishnet_points(out_fishnet, points, per_origin_values)

    if out_unreachable:
        # 不可达点：合并所有出发点的不可达点（按 origin_id 标注）
        all_unreach = []
        for origin_id in range(len(origins)):
            _, _, _, unreach = split_reachable(points, per_origin_values[origin_id])
            for p in unreach:
                all_unreach.append((p, origin_id))
        # 去重（同一点对多出发点均不可达时只记一次）
        seen = set()
        uniq = []
        for p, oid in all_unreach:
            if p.oid in seen:
                continue
            seen.add(p.oid)
            uniq.append((p, oid))
        # 写不可达点（合并去重后单图层）
        write_unreachable(out_unreachable, [p for p, _ in uniq], origin_id=0,
                          multi_origin=False)

    n_reachable = sum(1 for v in per_origin_values.values() for x in v if x is not None)
    n_total = len(origins) * len(points)
    return {
        "n_origins": len(origins),
        "n_points": len(points),
        "n_reachable": n_reachable,
        "n_unreachable": n_total - n_reachable,
        "n_bands": len(bands),
    }
