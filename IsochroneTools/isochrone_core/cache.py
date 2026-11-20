"""断点续跑存储 / 结果缓存。

从原项目 cache.py 移植（纯标准库 csv + threading）。append-only CSV
（origin_id, oid, duration_min, distance_km），重复运行自动跳过已完成的点对；
同一键后写的记录覆盖先写的（容忍中断残留）。
"""

from __future__ import annotations

import csv
import os
import threading

FIELDS = ["origin_id", "oid", "duration_min", "distance_km"]


def _parse_float(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class ResultStore:
    """append-only 结果表。线程安全，进程内单实例使用。"""

    def __init__(self, path: str):
        self.path = path
        self._map: dict[tuple[int, int], tuple[float | None, float | None]] = {}
        self._lock = threading.Lock()

        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        key = (int(row["origin_id"]), int(row["oid"]))
                    except (KeyError, TypeError, ValueError):
                        continue  # 容忍旧格式/坏行
                    self._map[key] = (
                        _parse_float(row.get("duration_min")),
                        _parse_float(row.get("distance_km")),
                    )
        else:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(FIELDS)

    def done_keys(self, origin_id: int | None = None) -> set[tuple[int, int]]:
        """已有有效 duration 的 (origin_id, oid) 集合。"""
        with self._lock:
            return {
                k for k, (dur, _) in self._map.items()
                if dur is not None and (origin_id is None or k[0] == origin_id)
            }

    def record(self, origin_id: int, oid: int,
               duration_min: float | None, distance_km: float | None = None) -> None:
        with self._lock:
            self._map[(origin_id, oid)] = (duration_min, distance_km)
            with open(self.path, "a", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow([
                    origin_id, oid,
                    "" if duration_min is None else duration_min,
                    "" if distance_km is None else distance_km,
                ])

    def get(self, origin_id: int, oid: int) -> tuple[float | None, float | None]:
        with self._lock:
            return self._map.get((origin_id, oid), (None, None))

    def rows(self) -> list[dict]:
        with self._lock:
            return [
                {"origin_id": k[0], "oid": k[1],
                 "duration_min": v[0], "distance_km": v[1]}
                for k, v in sorted(self._map.items())
            ]
