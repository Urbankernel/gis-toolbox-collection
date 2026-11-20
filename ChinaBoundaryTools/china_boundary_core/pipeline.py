"""主流程：参数归一化 → 抓取 → 导出；以及名称/编码解析预览。

替代原项目 cli.py 的角色，作为 .pyt 工具的执行入口。核心逻辑复用原项目的
config 校验语义 + fetch.fetch_features，但去掉 YAML 配置层（工具箱参数直接覆盖）。
"""
from __future__ import annotations

import logging

from .client import GeoAtlasClient
from .fetch import fetch_features
from .resolver import (AmbiguousName, DEFAULT_DICT_PATH, ResolveError, Resolver,
                       infer_level_from_adcode, infer_level_from_name)
from .store import Store

logger = logging.getLogger("chinaboundary.pipeline")

LEVELS = {"country", "province", "city", "district"}
MAX_DEPTH = 3


def _split_csv(value) -> list[str]:
    """把单个值拆成去空格、去空的列表；支持逗号分隔或已是列表的输入。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value).split(",") if x.strip()]


def _build_cfg(level=None, name=None, adcode=None, depth=0, children_only=False) -> dict:
    """把工具箱参数归一化为 fetch 所需的 cfg（复用原项目 config.validate 语义）。"""
    lvl = (level or "").strip().lower()
    if lvl == "auto" or lvl == "":
        lvl = None
    if lvl is not None and lvl not in LEVELS:
        raise ValueError(f"非法 level：{lvl}，可选 {sorted(LEVELS)}")

    names = _split_csv(name)
    adcodes = _split_csv(adcode)
    if names and adcodes:
        raise ValueError("name 与 adcode 不能同时指定")

    if lvl == "country" and (names or adcodes):
        logger.warning("level=country 时无需 name/adcode，已忽略，直接抓取中国国界")

    try:
        depth = int(depth or 0)
    except (TypeError, ValueError):
        raise ValueError(f"非法 depth：{depth}，范围 0~{MAX_DEPTH}")
    if depth < 0 or depth > MAX_DEPTH:
        raise ValueError(f"非法 depth：{depth}，范围 0~{MAX_DEPTH}")
    if children_only and depth == 0:
        logger.warning("children_only 需配合下钻，已自动将 depth 设为 1")
        depth = 1

    return {
        "level": lvl,
        "names": names,
        "adcodes": adcodes,
        "depth": depth,
        "children_only": bool(children_only),
    }


def _make_resolver(client, dict_path=None):
    return Resolver(client, dict_path or DEFAULT_DICT_PATH)


def run_boundary(level=None, name=None, adcode=None, depth=0, children_only=False,
                 out_fc=None, out_geojson=None, cache_dir=None, log=None) -> dict:
    """获取行政区边界，写出面要素类（.shp）+ 可选 GeoJSON。

    返回 {"features": int, "targets": list, "out_fc": str|None, "out_geojson": str|None}。
    """
    # 延迟导入：预览工具(ResolvePreview)复用本模块但不需要几何导出，避免拖入 shapely
    from .export import export_geojson, export_vector

    log = log or (lambda m: None)
    cfg = _build_cfg(level, name, adcode, depth, children_only)

    store = Store(cache_dir, enabled=bool(cache_dir)) if cache_dir else None
    client = GeoAtlasClient(store=store)
    resolver = _make_resolver(client)

    features, targets = fetch_features(cfg, client, resolver)
    log(f"已解析 {len(targets)} 个目标，抓取到 {len(features)} 个边界要素")

    out_fc_path = None
    if out_fc:
        out_fc_path = export_vector(features, out_fc)
        log(f"已导出边界面要素类：{out_fc_path}")
    out_geojson_path = None
    if out_geojson:
        out_geojson_path = export_geojson(features, out_geojson)
        log(f"已导出 GeoJSON：{out_geojson_path}")

    return {
        "features": len(features),
        "targets": targets,
        "out_fc": out_fc_path,
        "out_geojson": out_geojson_path,
    }


def preview_resolve(level=None, name=None, adcode=None, log=None) -> list[dict]:
    """名称/编码解析预览：纯本地字典解析，不发任何网络请求。

    返回解析结果列表，每项 {"input", "kind", "public_adcode", "backend_adcode",
    "level", "path"}；歧义/无边界/未知会抛 ResolveError（带候选列表信息）。
    """
    log = log or (lambda m: None)
    cfg = _build_cfg(level, name, adcode)
    resolver = _make_resolver(client=None)

    results: list[dict] = []
    if cfg["level"] == "country":
        targets = [("adcode", "100000")]
    elif cfg["adcodes"]:
        targets = [("adcode", c) for c in cfg["adcodes"]]
    elif cfg["names"]:
        targets = [("name", n) for n in cfg["names"]]
    else:
        targets = [("adcode", "100000")]

    for kind, val in targets:
        if kind == "adcode":
            public, backend, lvl = resolver.resolve_adcode(val, cfg["level"])
        else:
            public, backend, lvl = resolver.resolve(val, cfg["level"])
        results.append({
            "input": val,
            "kind": kind,
            "public_adcode": public,
            "backend_adcode": backend,
            "level": lvl,
            "path": resolver.describe(public),
        })
    return results
