# -*- coding: utf-8 -*-
"""编排：地名清单 → 检索 → 过滤 → 名称相关性 → 取舍 → 抠边界 → 排序/面积过滤。

返回 AOIRecord 列表（geometry 为 GeoJSON dict，WGS-84）与逐地名成败汇总，
不依赖 arcpy，可命令行 pytest（provider 用 mock）。
"""
from typing import Callable, List, Optional

from .filter import (deduplicate_candidates, filter_candidates,
                     relevant_candidates, select_candidates)
from .matcher import match_best
from .models import AOIRecord, Candidate, POIInput, Status
from .providers import AmapProvider, BaiduProvider


def _fill_record(rec: AOIRecord, cand: Candidate, input_name: str) -> AOIRecord:
    """回填候选的名称/分类/地址，并记录用户输入地名。"""
    rec.name = cand.name
    rec.poi_type = cand.poi_type
    rec.address = cand.address
    rec.input_name = input_name
    return rec


def process_name(provider, poi: POIInput, want: str = "all",
                 keyword: Optional[str] = None,
                 keep_types: Optional[List[str]] = None,
                 min_area: float = 0.0, max_area: float = 1e9):
    """处理单个地名，返回 (records, status)。

    流程：检索 → 去重 → 分类/父子过滤 → 名称相关性 → 取舍 → 抠边界 →
    （main 模式四维排序取最优 / all·keyword 模式面积过滤后全保留）。
    """
    candidates = provider.search(poi.name, poi.city)
    candidates = deduplicate_candidates(candidates)
    candidates = filter_candidates(candidates, keep_types=keep_types,
                                   query_name=poi.name)
    candidates = relevant_candidates(candidates, poi.name)
    candidates = select_candidates(candidates, want, keyword)

    if not candidates:
        return [], Status.NO_CANDIDATE

    user_loc = None
    if poi.lng is not None and poi.lat is not None:
        user_loc = (poi.lng, poi.lat)

    if want == "main":
        entries = []
        for c in candidates:
            rec = provider.fetch_boundary(c.poi_id, user_loc=user_loc)
            entries.append((c, rec))
        best = match_best(entries, poi.name, ref_location=user_loc,
                          min_area_km2=min_area, max_area_km2=max_area)
        if best is None:
            return [], Status.NO_GEOMETRY
        _score, cand, rec = best
        return [_fill_record(rec, cand, poi.name)], Status.MATCHED

    # want ∈ {all, keyword}：全部抠边界，按面积过滤后保留
    records = []
    for c in candidates:
        rec = provider.fetch_boundary(c.poi_id, user_loc=user_loc)
        if rec is None or rec.geometry is None:
            continue
        if not (min_area <= rec.area_km2 <= max_area):
            continue
        records.append(_fill_record(rec, c, poi.name))
    if records:
        return records, Status.MATCHED
    return [], Status.NO_GEOMETRY


def process_names(provider, names: List[POIInput], want: str = "all",
                  keyword: Optional[str] = None,
                  keep_types: Optional[List[str]] = None,
                  min_area: float = 0.0, max_area: float = 1e9,
                  on_progress: Optional[Callable[[int, int, str], None]] = None):
    """处理地名清单，返回 (records, summary)。

    summary 每项为 {"name", "status", "matched_count"}。
    """
    records: List[AOIRecord] = []
    summary = []
    total = len(names)
    for i, poi in enumerate(names):
        if on_progress:
            on_progress(i, total, poi.name)
        recs, status = process_name(provider, poi, want=want, keyword=keyword,
                                    keep_types=keep_types, min_area=min_area,
                                    max_area=max_area)
        records.extend(recs)
        summary.append({"name": poi.name, "status": status.value,
                        "matched_count": len(recs)})
    return records, summary


def make_provider(provider: str, api_key: str, *,
                  max_candidates: int = 25, min_interval: float = 3.0,
                  max_interval: float = 10.0, retries: int = 3,
                  timeout: float = 15.0, boundary_cookie: Optional[str] = None,
                  boundary_ua: Optional[str] = None):
    """按名称创建 Provider 实例（供 .pyt 调用）。"""
    from .http import HttpClient

    http = HttpClient(min_interval=min_interval, max_interval=max_interval,
                      retries=retries, timeout=timeout)
    boundary_headers = {}
    if boundary_cookie:
        http.cookie = boundary_cookie
        boundary_headers["Cookie"] = boundary_cookie
    if boundary_ua:
        http.user_agent = boundary_ua
        boundary_headers["User-Agent"] = boundary_ua

    if provider == "amap":
        return AmapProvider(api_key, http=http, max_candidates=max_candidates,
                            boundary_headers=boundary_headers)
    if provider == "baidu":
        return BaiduProvider(api_key, http=http, max_candidates=max_candidates)
    raise ValueError(f"不支持的数据源：{provider}（仅 amap / baidu）")


# ------------------------------------------------------------------ 地名清单解析


def _to_float(v) -> Optional[float]:
    """安全转 float：空串/None 返回 None。"""
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _first(row: dict, *keys) -> str:
    """从 CSV 行里取第一个非空值（支持中英文列名）。"""
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def parse_names_csv(csv_path: str) -> List[POIInput]:
    """读 CSV 地名清单，返回 POIInput 列表。

    列名支持英文 name/city/lng/lat 及中文 名称/地名/城市/经度/纬度。
    """
    import csv
    inputs = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = _first(row, "name", "名称", "地名")
            if not name:
                continue
            city = _first(row, "city", "城市") or None
            lng = _to_float(_first(row, "lng", "lon", "经度", "longitude"))
            lat = _to_float(_first(row, "lat", "纬度", "latitude"))
            inputs.append(POIInput(name=name, city=city, lng=lng, lat=lat))
    return inputs


def parse_names_text(text: str, city: Optional[str] = None) -> List[POIInput]:
    """解析多行地名文本，每行一个地名，可带可选列。

    行格式：name[,city[,lng[,lat]]]；`#` 开头为注释。
    """
    inputs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        name = parts[0]
        if not name:
            continue
        c = parts[1] if len(parts) > 1 and parts[1] else None
        lng = _to_float(parts[2]) if len(parts) > 2 else None
        lat = _to_float(parts[3]) if len(parts) > 3 else None
        inputs.append(POIInput(name=name, city=c or city, lng=lng, lat=lat))
    return inputs


def _read_text_file(path: str) -> str:
    """读文本文件，按 UTF-8-SIG → GBK → UTF-8 依次尝试解码，兜底替换。"""
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def parse_names_txt(txt_path: str, city: Optional[str] = None) -> List[POIInput]:
    """读 TXT 地名清单文件，每行一个地名，返回 POIInput 列表。

    兼容 UTF-8（含 BOM）/ GBK 编码；`#` 开头为注释；
    城市统一由全局 city 兜底（行内逗号仍可带 city/lng/lat，见 parse_names_text）。
    """
    return parse_names_text(_read_text_file(txt_path), city=city)
