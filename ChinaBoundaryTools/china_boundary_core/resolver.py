"""名称 <-> adcode 解析（基于预置字典 data/dict.json）。

字典（dict.json v2）采用「公开主码 = 高德官方，后端抓取码 = DataV.GeoAtlas」设计，
已随工具箱打包在 ChinaBoundaryTools/data/dict.json，运行时直接加载、无需在线构建：

- records: 以高德官方最新 adcode 为键（公开主码），每条含 ``backend_adcode``
  （GeoAtlas 后台实际使用的旧码，抓取时用它）。
- orphans: 高德官方有、但 GeoAtlas 后台暂无边界数据的行政区（输入时给出提示）。
- backend_index: GeoAtlas 旧码 -> 公开主码，用于向后兼容旧码输入与抓取结果回填。

改造点：字典定位由原项目的 cwd 反推改为基于 ``__file__`` 反推工具箱目录下的
``data/dict.json``（不依赖运行工作目录）。
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("chinaboundary.resolver")

# 本文件位于 ChinaBoundaryTools/china_boundary_core/，data/dict.json 位于
# ChinaBoundaryTools/data/，故向上两级再进 data。
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLBOX_DIR = os.path.dirname(_PACKAGE_DIR)
_DATA_DIR = os.path.join(_TOOLBOX_DIR, "data")


def _candidate_dict_paths() -> list[str]:
    return [
        os.path.join(_DATA_DIR, "dict.json"),
        os.path.join(os.getcwd(), "data", "dict.json"),   # 兜底：运行目录下的 data
    ]


def default_dict_path() -> str:
    """返回默认字典路径：优先返回已存在的候选，否则返回第一个候选（用于报错提示）。"""
    for p in _candidate_dict_paths():
        if os.path.exists(p):
            return p
    return _candidate_dict_paths()[0]


DEFAULT_DICT_PATH = default_dict_path()

# 行政后缀（长后缀在前，保证「自治区」优先于「区」剥离）
SUFFIXES = (
    "特别行政区", "自治区", "自治州", "自治县", "自治旗",
    "地区", "盟", "市", "县", "区", "旗", "林区", "特区", "省",
)


class ResolveError(RuntimeError):
    """名称/编码解析失败。"""


class AmbiguousName(ResolveError):
    def __init__(self, name: str, candidates: list[dict], message: str):
        self.name = name
        self.candidates = candidates
        super().__init__(message)


def strip_suffix(name: str) -> str:
    n = (name or "").strip()
    for s in SUFFIXES:
        if n.endswith(s):
            return n[: -len(s)]
    return n


def infer_level_from_adcode(adcode) -> str | None:
    a = str(adcode).strip()
    if a == "100000":
        return "country"
    if len(a) == 6 and a.isdigit():
        if a[2:] == "0000":
            return "province"
        if a[4:] == "00":
            return "city"
        return "district"
    return None


def infer_level_from_name(name: str) -> str | None:
    n = (name or "").strip()
    if n.endswith(("省", "自治区", "特别行政区")):
        return "province"
    if n.endswith(("市", "地区", "盟", "自治州")):
        return "city"
    if n.endswith(("区", "县", "旗", "林区", "特区")):
        return "district"
    return None


def load_dictionary(cache_path: str | None) -> dict | None:
    """加载预置字典（v2）；文件不存在 / 格式不符 / 读取失败返回 None。"""
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("version") == 2:
                return data
            logger.warning("字典格式过旧（非 v2）")
        except Exception as e:
            logger.warning("读取字典失败：%s", e)
    return None


class Resolver:
    def __init__(self, client, cache_path: str | None):
        self.client = client
        self.cache_path = cache_path
        self.dictionary = load_dictionary(cache_path)

    def ensure_dictionary(self) -> dict:
        if self.dictionary is None:
            raise ResolveError(
                f"未找到行政区划字典：{self.cache_path or DEFAULT_DICT_PATH}。"
                "字典已随工具箱打包（data/dict.json），请确认文件存在且为 v2 格式。"
            )
        return self.dictionary

    def _records(self) -> dict:
        d = self.ensure_dictionary()
        return d.get("records", {}) if isinstance(d, dict) else {}

    def _orphans(self) -> dict:
        d = self.ensure_dictionary()
        return d.get("orphans", {}) if isinstance(d, dict) else {}

    def _backend_index(self) -> dict:
        d = self.ensure_dictionary()
        return d.get("backend_index", {}) if isinstance(d, dict) else {}

    def _get(self, code) -> dict | None:
        return self._records().get(str(code))

    def public_adcode(self, backend_code) -> str:
        """后端旧码 -> 公开主码（不在索引中则原样返回）。"""
        return self._backend_index().get(str(backend_code), str(backend_code))

    def remap_feature(self, feature: dict) -> dict:
        """把抓取结果里的 adcode / parent / name 从后端旧码回填为公开主码语义。"""
        records = self._records()
        code = str(feature.get("adcode") or "")
        public = self.public_adcode(code)
        feature["adcode"] = public
        if feature.get("parent"):
            feature["parent"] = self.public_adcode(feature["parent"])
        info = records.get(public)
        if info and info.get("name"):
            feature["name"] = info["name"]
        return feature

    def resolve(self, name: str, level: str | None = None) -> tuple[str, str, str]:
        """按名称解析，返回 (公开主码, 后端抓取码, level)；歧义抛 AmbiguousName。"""
        records = self._records()
        orphans = self._orphans()
        q = (name or "").strip()
        q_short = strip_suffix(q)

        exact_full, exact_short, prefix = [], [], []
        for code, info in records.items():
            nm = info.get("name") or ""
            if not nm:
                continue
            if nm == q:
                exact_full.append((code, info))
            elif strip_suffix(nm) == q_short:
                exact_short.append((code, info))
            elif nm.startswith(q):
                prefix.append((code, info))

        candidates = exact_full or exact_short or prefix

        # orphan 名称命中（用于给出「暂无边界」而非「未找到」）
        orphan_hit = None
        for code, info in orphans.items():
            nm = info.get("name") or ""
            if nm == q or strip_suffix(nm) == q_short:
                orphan_hit = (code, info)
                break

        if level:
            level_matched = [c for c in candidates if c[1].get("level") == level]
            if level_matched:
                candidates = level_matched
            elif candidates:
                found = sorted({c[1].get("level") for c in candidates})
                raise ResolveError(f"名称「{name}」未匹配到 {level} 层级（找到层级：{found}），请检查 --level 或名称")

        if not candidates:
            if orphan_hit:
                code, info = orphan_hit
                raise ResolveError(f"该行政区「{info.get('name')}（{code}）」在 DataV 后台暂无边界数据，无法抓取")
            raise ResolveError(f"未找到名称「{name}」对应的行政区（level={level or '任意'}）")

        uniq: dict[str, dict] = {}
        for code, info in candidates:
            uniq.setdefault(code, info)
        candidates = list(uniq.items())

        if len(candidates) == 1:
            code, info = candidates[0]
            return code, info.get("backend_adcode", code), info.get("level")

        lines = "\n".join(
            f"  - {self.describe(c)}  (adcode={c}, level={i.get('level')})"
            for c, i in candidates
        )
        raise AmbiguousName(q, [i for _, i in candidates], f"名称「{name}」存在多个匹配，请用 --adcode 或 --level 消除歧义：\n{lines}")

    def resolve_adcode(self, adcode, level: str | None = None) -> tuple[str, str, str]:
        """按 adcode 解析，返回 (公开主码, 后端抓取码, level)；orphan 给出提示。"""
        code = str(adcode).strip()
        records = self._records()
        orphans = self._orphans()
        backend_index = self._backend_index()

        if code in records:
            info = records[code]
            public = code
        elif code in backend_index:
            public = backend_index[code]
            info = records.get(public)
            if info is None:
                raise ResolveError(f"未知行政区编码：{adcode}")
        elif code in orphans:
            info = orphans[code]
            raise ResolveError(f"该行政区「{info.get('name')}（{code}）」在 DataV 后台暂无边界数据，无法抓取")
        else:
            raise ResolveError(f"未知行政区编码：{adcode}")

        lvl = info.get("level")
        if level and lvl and level != lvl:
            logger.warning("--level=%s 与实际层级 %s 不一致，以实际层级为准", level, lvl)
        return public, info.get("backend_adcode", public), lvl

    def describe(self, adcode) -> str:
        """返回 adcode 的完整层级路径（如 广东省 / 广州市 / 天河区）。"""
        records = self._records()
        path: list[str] = []
        cur = self.public_adcode(adcode)
        seen: set[str] = set()
        while cur and cur in records and cur not in seen:
            seen.add(cur)
            info = records[cur]
            if info.get("name") and info.get("level") != "country":
                path.append(info["name"])
            cur = info.get("parent")
        return " / ".join(reversed(path)) or str(adcode)
