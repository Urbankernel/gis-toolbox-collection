"""主流程（替代原项目 cli.py）：城市列表查询 + 空气质量采集导出。"""

from __future__ import annotations

import csv
import os
from typing import Any, Callable

from . import export
from .client import CnemcClient
from .fetch import fetch, resolve_city_by_list
from .normalize import to_float, to_text
from .store import load_progress, save_progress

_log = Callable[[str], None]


# --------------------------------------------------------------------------- #
# 城市目标解析
# --------------------------------------------------------------------------- #
def _read_cities_file(path: str) -> list[dict]:
    """从清单文件读取城市目标：每行一个城市名或编码（纯数字视为编码）。"""
    if not os.path.exists(path):
        raise ValueError(f"城市清单文件不存在: {path}")
    targets: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            s = s.split("#", 1)[0].strip()
            if not s:
                continue
            if s.isdigit():
                targets.append({"code": s})
            else:
                targets.append({"name": s})
    return targets


def parse_targets(city_names: str | None, city_codes: str | None,
                  cities_file: str | None) -> list[dict]:
    """解析城市目标：name/code 按位置配对，清单文件优先。"""
    name_list = [x.strip() for x in (city_names or "").split(",") if x.strip()]
    code_list = [x.strip() for x in (city_codes or "").split(",") if x.strip()]
    targets: list[dict] = []
    if cities_file:
        targets.extend(_read_cities_file(cities_file))
    for i in range(max(len(name_list), len(code_list))):
        name = name_list[i] if i < len(name_list) else None
        code = code_list[i] if i < len(code_list) else None
        if name or code:
            targets.append({"name": name, "code": code})
    if not targets:
        raise ValueError("未指定城市：请填写城市名、城市编码或城市清单文件（至少一项）")
    return targets


# --------------------------------------------------------------------------- #
# 工具 2：城市列表查询
# --------------------------------------------------------------------------- #
def list_cities(*, city_filter: str | None = None, out_csv: str | None = None,
                qps: float = 1.0, log: _log | None = None) -> dict:
    """拉取全国城市列表（编码/名称/经纬度/AQI），按城市名筛选（逗号分隔多个），可选写 CSV。"""
    log = log or (lambda m: None)
    client = CnemcClient(qps=qps, max_retries=2, timeout=30.0)
    cities = client.get_all_cities()
    if not isinstance(cities, list):
        raise RuntimeError("城市列表接口返回异常，接口可能已变化")

    kws = [x.strip() for x in (city_filter or "").split(",") if x.strip()]
    rows = []
    for c in cities:
        area = to_text(c.get("Area")) or ""
        if kws and not any(k in area for k in kws):
            continue
        lon = to_float(c.get("Longitude"))
        lat = to_float(c.get("Latitude"))
        rows.append({
            "city_code": to_text(c.get("CityCode")) or "",
            "city_name": area,
            "lon": lon,
            "lat": lat,
            "aqi": to_float(c.get("AQI")),
            "quality": to_text(c.get("Quality")) or "",
        })

    out_path = None
    if out_csv:
        out_path = out_csv if out_csv.lower().endswith(".csv") else out_csv + ".csv"
        parent = os.path.dirname(os.path.abspath(out_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=["city_code", "city_name", "lon", "lat", "aqi", "quality"])
            writer.writeheader()
            for r in rows:
                writer.writerow({k: ("" if r.get(k) is None else r.get(k))
                                 for k in ("city_code", "city_name", "lon", "lat", "aqi", "quality")})
        log(f"城市列表已写入: {out_path}")

    log(f"共 {len(rows)} 个城市" + (f"（匹配 {kws}）" if kws else ""))
    return {"count": len(rows), "rows": rows, "out_csv": out_path}


# --------------------------------------------------------------------------- #
# 工具 1：获取空气质量数据
# --------------------------------------------------------------------------- #
def run_air(
    *,
    city_names: str | None = None,
    city_codes: str | None = None,
    cities_file: str | None = None,
    fetch_city: bool = True,
    fetch_stations: bool = True,
    fetch_station_history: bool = False,
    qps: float = 1.0,
    out_dir: str | None = None,
    csv_on: bool = True,
    json_on: bool = True,
    log: _log | None = None,
) -> dict:
    """主流程：解析城市 → 逐城市采集 → 导出汇总成果（断点续跑）。"""
    log = log or (lambda m: None)
    if not out_dir:
        raise ValueError("未指定输出目录")
    os.makedirs(out_dir, exist_ok=True)

    targets = parse_targets(city_names, city_codes, cities_file)
    client = CnemcClient(qps=qps)

    # 拉取一次全国城市列表，供多个城市本地匹配复用
    all_cities = client.get_all_cities()
    if not isinstance(all_cities, list) or not all_cities:
        raise RuntimeError("城市列表接口返回为空或结构异常，接口可能已变化")

    # 解析所有目标城市（单个失败不中断）
    metas: list[dict] = []
    resolve_errors: list[tuple[str, str]] = []
    for t in targets:
        label = t.get("name") or t.get("code") or "?"
        try:
            metas.append(resolve_city_by_list(all_cities, name=t.get("name"),
                                              code=t.get("code")))
        except Exception as e:
            resolve_errors.append((label, str(e)))
            log(f"城市解析失败: {label} — {e}")
    if not metas:
        raise RuntimeError("没有可采集的城市（全部解析失败），请检查城市名/编码")

    # 断点续跑
    progress_path = os.path.join(out_dir, "progress.json")
    progress = load_progress(progress_path)

    city_results: list[dict] = []
    for meta in metas:
        city_name = meta.get("city_name") or "unknown"
        if progress.get(city_name) == "done":
            log(f"跳过已完成城市: {city_name}")
            continue
        try:
            result = fetch(
                client, meta,
                fetch_city=fetch_city,
                fetch_stations=fetch_stations,
                fetch_station_history=fetch_station_history,
            )
            progress[city_name] = "done"
            city_results.append({"meta": meta, "result": result})
            rt = result.get("city_realtime")
            log(f"完成 {city_name}（{meta.get('city_code')}）: AQI "
                f"{rt.get('aqi') if rt else None} | 点位 {len(result.get('stations', []))} 个")
        except Exception as e:
            progress[city_name] = "failed"
            log(f"城市 {city_name} 采集失败: {e}")
        save_progress(progress_path, progress)

    # 汇总导出（单/多城市统一走 merge，字段 schema 一致，靠 city_code/city_name 区分）
    out_files: list[str] = []
    if city_results:
        merged = export.merge_results([r["result"] for r in city_results])
        out_files.extend(export.export_table(merged, out_dir, csv_on=csv_on, json_on=json_on))
        out_files.extend(export.export_vector(merged, out_dir))
    else:
        log("所有城市均已完成或失败，未产生新成果")

    done = [k for k, v in progress.items() if v == "done"]
    failed = [k for k, v in progress.items() if v == "failed"]
    summary = {
        "out_dir": out_dir,
        "out_files": out_files,
        "done": done,
        "failed": failed,
        "resolve_errors": resolve_errors,
        "request_count": client.request_count,
        "city_count": len(city_results),
    }
    log(f"完成 {len(done)} 个城市"
        + (f"，失败 {len(failed)} 个: {', '.join(failed)}" if failed else ""))
    log(f"实际请求: {client.request_count} 次")
    log(f"输出目录: {out_dir}")
    return summary
