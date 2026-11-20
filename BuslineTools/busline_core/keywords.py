# -*- coding: utf-8 -*-
"""检索关键字构造（高德公交）：lines / only / full（数字穷举）三种模式。

优先级：only > lines > full；suffix / extra 只在 full 模式生效。
"""
from __future__ import annotations

from typing import Optional


def _split(s: Optional[str]) -> list[str]:
    """逗号/分号分隔字符串 → 去空列表。"""
    if not s:
        return []
    parts = []
    for seg in str(s).replace(";", ",").split(","):
        seg = seg.strip()
        if seg:
            parts.append(seg)
    return parts


def build_keywords(
    mode: str,
    *,
    lines: Optional[str] = None,
    only: Optional[str] = None,
    prefix: Optional[str] = None,
    suffix: Optional[str] = None,
    extra: Optional[str] = None,
    max_num: int = 1000,
) -> list[str]:
    """构造检索关键字队列。

    参数:
        mode: "lines"（指定线路）/ "only"（只检索关键词）/ "full"（数字穷举）
        lines: 线路名清单，逗号分隔；可与 prefix 组合成「前缀+线路」
        only: 只检索关键词，逗号分隔，优先级最高
        prefix: 前缀，逗号分隔（lines 模式拼在每条线路前；full 模式拼在数字前）
        suffix: 数字后缀，逗号分隔（仅 full 模式）
        extra: 额外关键词，逗号分隔（仅 full 模式，追加到穷举队列末尾）
        max_num: 数字穷举上限
    """
    mode = (mode or "full").strip().lower()

    only_list = _split(only)
    if only_list:
        return only_list

    prefixes = _split(prefix)
    suffixes = _split(suffix)
    extras = _split(extra)

    if mode == "lines":
        lines_list = _split(lines)
        kws: list[str] = []
        for ln in lines_list:
            if prefixes:
                for p in prefixes:
                    kws.append(p + ln)
            else:
                kws.append(ln)
        return kws

    # full：数字穷举（prefix × 数字 × suffix 笛卡尔积） + extra 追加
    kws = []
    for n in range(1, int(max_num) + 1):
        num = str(n)
        if prefixes and suffixes:
            for p in prefixes:
                for sfx in suffixes:
                    kws.append(f"{p}{num}{sfx}")
        elif prefixes:
            for p in prefixes:
                kws.append(f"{p}{num}")
        elif suffixes:
            for sfx in suffixes:
                kws.append(f"{num}{sfx}")
        else:
            kws.append(num)
    kws.extend(extras)
    return kws
