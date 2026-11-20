"""抓取编排：解析目标 -> 递归下钻 -> 回填公开主码 -> 组装 feature 列表。

支持一次抓取多个目标（`name: 山东省,河北省` 或 `adcode: 370000,130000`），
并通过 `depth` 参数递归下钻多级（0=本级，1=直接子级，2=孙级……）。

近原样移植自 Urbankernel/chinaboundary。
"""
from __future__ import annotations

import logging

from . import normalize
from .resolver import Resolver

logger = logging.getLogger("chinaboundary.fetch")


def _target_inputs(cfg: dict) -> list[tuple[str, str]]:
    """返回 [(kind, value), ...]，kind 为 name 或 adcode；无输入时默认国家。"""
    # country 固定抓国家本体（100000），忽略 name/adcode
    if cfg.get("level") == "country":
        return [("adcode", "100000")]
    adcodes = cfg.get("adcodes") or []
    names = cfg.get("names") or []
    if adcodes:
        return [("adcode", c) for c in adcodes]
    if names:
        return [("name", n) for n in names]
    return [("adcode", "100000")]


def _descend(client, backend_code: str, remaining_depth: int) -> list[dict]:
    """递归下钻 backend_code 的子级及以下（remaining_depth 层），不含 backend_code 自身。

    终止条件：remaining_depth 减到 0，或子级为空（叶子节点/直筒子市/台湾）。
    注意：children() 返回的每个 feature 本身就是子级的完整边界，直接并入结果即可。
    """
    out: list[dict] = []
    if remaining_depth <= 0:
        return out
    children = normalize.parse_response(client.children(backend_code))
    out.extend(children)
    for child in children:
        child_code = child.get("adcode")
        if child_code:
            out.extend(_descend(client, child_code, remaining_depth - 1))
    return out


def _collect(client, backend_code: str, remaining_depth: int, is_root: bool, children_only: bool) -> list[dict]:
    """取本级（children_only 时跳过根）+ 递归下钻子级/孙级。"""
    out: list[dict] = []
    if not (is_root and children_only):
        out.extend(normalize.parse_response(client.self_boundary(backend_code)))
    out.extend(_descend(client, backend_code, remaining_depth))
    return out


def fetch_features(cfg: dict, client, resolver: Resolver) -> tuple[list[dict], list[dict]]:
    """抓取多个目标，返回 (features, targets)。

    targets 每个元素为 {"input", "public_adcode", "backend_adcode", "level", "depth"}。
    """
    level = cfg.get("level")
    depth = int(cfg.get("depth", 0))
    children_only = cfg.get("children_only", False)

    features: list[dict] = []
    targets: list[dict] = []
    for kind, val in _target_inputs(cfg):
        if kind == "adcode":
            public_code, backend_code, target_level = resolver.resolve_adcode(val, level)
        else:
            public_code, backend_code, target_level = resolver.resolve(val, level)

        logger.info(
            "目标：%s（公开码=%s, 后端码=%s, level=%s, depth=%d）",
            val, public_code, backend_code, target_level, depth,
        )

        features.extend(_collect(client, backend_code, depth, is_root=True, children_only=children_only))

        targets.append({
            "input": val,
            "public_adcode": public_code,
            "backend_adcode": backend_code,
            "level": target_level,
            "depth": depth,
        })

    # 回填公开主码：adcode / parent / name 从后端旧码映射回公开码，保证输出与输入语义一致
    features = [resolver.remap_feature(f) for f in features]

    # 按公开主码去重（多目标下钻可能产生重叠，如 广东省 + 广州市 均 depth=2）
    seen: set[str] = set()
    deduped: list[dict] = []
    for f in features:
        code = str(f.get("adcode") or "")
        if code and code in seen:
            continue
        if code:
            seen.add(code)
        deduped.append(f)
    features = deduped

    if not features:
        logger.warning("未获取到任何边界要素")
    return features, targets
