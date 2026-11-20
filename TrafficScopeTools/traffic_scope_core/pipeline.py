"""路况采集主流程：范围 → 切瓦片 → 采集（限速/断点续跑）→ 分类 → 镶嵌 →
范围裁剪 → 栅格 + 矢量导出 → 元数据。

日志通过 messages.addMessage 输出（ArcGIS 工具箱 messages 对象）。
"""

from __future__ import annotations

import os
from datetime import datetime

from geo_common.ratelimit import RateLimiter

from . import export as exp
from .cache import RunContext
from .coords import tile_key, tile_polygon, tiles_for_bbox
from .estimate import resolve_level
from .providers import PROVIDERS, PROVIDER_LEVEL_RANGE, status_name
from .tiles import (assemble_mosaic, classify_tile, clip_to_polygon,
                    fetch_tile_png)


def _make_tiles(scope, level: int, provider: str):
    return [(tile_key(level, tx, ty), tile_polygon(tx, ty, level, provider))
            for tx, ty in tiles_for_bbox(scope.bounds, level, provider)]


def _collect_all(tile_pairs, level: int, provider: str, qps: float,
                 max_retries: int, ctx: RunContext, log) -> tuple[dict, list[str]]:
    """采集全部瓦片（qps 限速 + 断点续跑），返回 (tid→(png,sampled_at), 失败列表)。"""
    results: dict[str, tuple[bytes, str]] = {}
    failed: list[str] = []
    done = ctx.completed_tiles()

    # 断点续跑：已完成瓦片直接读 raw 存档
    for tid, _ in tile_pairs:
        if tid in done:
            env = ctx.read_tile_raw(tid)
            if env is not None:
                results[tid] = env
    if done:
        log(f"断点续跑：{len(done)} 个瓦片已完成（读存档），"
            f"剩余 {len(tile_pairs) - len(done)} 个待采集")

    limiter = RateLimiter(qps)
    for tid, _ in tile_pairs:
        if tid in results:
            continue
        lv, tx, ty = (int(v) for v in tid.split("_"))
        limiter.wait()
        sampled_at = datetime.now().isoformat(timespec="seconds")
        try:
            data = fetch_tile_png(tx, ty, lv, provider=provider,
                                  max_retries=max_retries)
            ctx.write_tile_raw(tid, sampled_at, data)
            ctx.record_tile(tid, "ok", sampled_at)
            results[tid] = (data, sampled_at)
        except Exception as e:
            log(f"  瓦片 {tid} 采集失败（已记录，可续跑重试）: {e}")
            ctx.record_tile(tid, "failed", "", str(e)[:200])
            failed.append(tid)

    return results, failed


def _classify_all(tile_pairs, provider: str, ctx: RunContext) -> dict:
    classified: dict = {}
    for tid, _ in tile_pairs:
        env = ctx.read_tile_raw(tid)
        if env is None:
            continue
        data, _sampled = env
        _lv, tx, ty = (int(v) for v in tid.split("_"))
        classified[(tx, ty)] = classify_tile(data, provider)
    return classified


def run_pipeline(scope, provider: str, level, qps: float, max_retries: int,
                 clip: bool, out_tif: str, out_tiles: str, work_dir: str,
                 log=print) -> str:
    """采集路况主流程，返回 run 工作目录。"""
    if provider not in PROVIDERS:
        raise ValueError(f"未知数据源 {provider!r}，可选 {list(PROVIDERS)}")

    area = scope.area_km2()
    lv = resolve_level(area, provider, level)
    lo, hi = PROVIDER_LEVEL_RANGE[provider]
    if not (lo <= lv <= hi):
        raise ValueError(f"level={lv} 超出 {provider} 范围 [{lo}, {hi}]")

    from .coords import resolution
    log(f"查询范围: {scope.name}，面积约 {area:.1f} km²，"
        f"provider={provider}，level={lv}，约 {resolution(lv, provider):.0f} m/px")

    tile_pairs = _make_tiles(scope, lv, provider)
    log(f"路况瓦片切分: {len(tile_pairs)} 个（无需 AK、无配额限制）")

    # 工作目录：未填则自动创建（out_tif 旁带时间戳目录）
    if not work_dir:
        work_dir = os.path.join(
            os.path.dirname(os.path.abspath(out_tif)),
            "traffic_scope_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    ctx = RunContext(work_dir)
    log(f"工作目录: {ctx.dir}")

    # —— 采集 ——
    results, failed = _collect_all(tile_pairs, lv, provider, qps,
                                   max_retries, ctx, log)
    if failed:
        log(f"注意：{len(failed)} 个瓦片失败，成果为部分数据（续跑可补采）")
    if not results:
        raise RuntimeError("没有任何瓦片采集成功，无法产出成果。")

    # —— 分类 + 镶嵌 + 裁剪 ——
    classified = _classify_all(tile_pairs, provider, ctx)
    mosaic = assemble_mosaic(lv, classified, provider)
    if clip:
        clip_to_polygon(mosaic, scope.polygon)
    px_nz = int((mosaic.status > 0).sum())
    log(f"拼接镶嵌 {mosaic.shape[1]}×{mosaic.shape[0]} px，"
        f"范围内路况像素 {px_nz} 个")
    if px_nz == 0:
        raise RuntimeError(
            f"范围内没有任何路况像素：请检查范围是否位于{PROVIDERS[provider]['label']}"
            "地图覆盖区，或稍后重试（服务端偶发空瓦片）。")

    # —— 摘要 ——
    px_by_status = {}
    for code in (1, 2, 3, 4):
        n = int((mosaic.status == code).sum())
        if n:
            px_by_status[status_name(code)] = n
    sampled_ats = [sa for _d, sa in results.values()]
    summary = {
        "provider": provider,
        "level": lv,
        "tiles_total": len(tile_pairs),
        "tiles_ok": len(results),
        "tiles_failed": failed,
        "mosaic_px": px_nz,
        "pixels_by_status": px_by_status,
        "sampled_at_min": min(sampled_ats, default=""),
        "sampled_at_max": max(sampled_ats, default=""),
    }
    log("像素状态分布: " + " · ".join(f"{k} {v}"
                                       for k, v in px_by_status.items()))

    # —— 导出 ——
    exp.write_classified_geotiff(mosaic.status, mosaic, out_tif, provider)
    log(f"输出路况栅格: {out_tif}")
    if out_tiles:
        exp.export_tiles(tile_pairs, out_tiles)
        log(f"输出瓦片网格: {out_tiles}")

    ctx.write_metadata(summary)
    log("完成。")
    return ctx.dir
