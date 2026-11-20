"""断点续跑：瓦片级进度（progress.csv）+ 原始 PNG 存档（raw/）。

每次运行落到一个工作目录；重跑时读取 progress.csv 已完成瓦片，
直接读 raw 存档，只补采未成功瓦片。仅用标准库，无第三方依赖。
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime


class RunContext:
    """一次运行的目录与瓦片进度管理。

    目录结构：
        work_dir/
            progress.csv        瓦片级进度（断点续跑依据）
            raw/tile_*.png     原始瓦片 PNG 存档（含 .time 采样时刻）
            metadata.json       运行元数据
    """

    def __init__(self, work_dir: str):
        self.dir = work_dir
        os.makedirs(self.raw_dir, exist_ok=True)
        self.started_at = datetime.now().isoformat(timespec="seconds")

    # ------------------------------------------------------------------ 路径

    @property
    def raw_dir(self) -> str:
        return os.path.join(self.dir, "raw")

    @property
    def progress_csv(self) -> str:
        return os.path.join(self.dir, "progress.csv")

    @property
    def metadata_json(self) -> str:
        return os.path.join(self.dir, "metadata.json")

    def tile_raw_path(self, tid: str) -> str:
        safe = str(tid).replace("/", "_")
        return os.path.join(self.raw_dir, f"tile_{safe}.png")

    # ------------------------------------------------------------------ 原始响应与进度

    def write_tile_raw(self, tid: str, sampled_at: str, png_bytes: bytes) -> None:
        with open(self.tile_raw_path(tid), "wb") as f:
            f.write(png_bytes)
        with open(self.tile_raw_path(tid) + ".time", "w", encoding="utf-8") as f:
            f.write(sampled_at)

    def read_tile_raw(self, tid: str) -> tuple[bytes, str] | None:
        path = self.tile_raw_path(tid)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            data = f.read()
        sampled = ""
        tp = path + ".time"
        if os.path.exists(tp):
            with open(tp, "r", encoding="utf-8") as f:
                sampled = f.read().strip()
        return data, sampled

    def record_tile(self, tid: str, status: str, sampled_at: str,
                    note: str = "") -> None:
        new = not os.path.exists(self.progress_csv)
        with open(self.progress_csv, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["tile_id", "status", "sampled_at", "note"])
            w.writerow([tid, status, sampled_at, note])

    def completed_tiles(self) -> set[str]:
        done: set[str] = set()
        if not os.path.exists(self.progress_csv):
            return done
        with open(self.progress_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "ok" and row.get("tile_id"):
                    done.add(row["tile_id"])
        return done

    def write_metadata(self, stats: dict) -> None:
        payload = {
            "started_at": self.started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "stats": stats,
        }
        with open(self.metadata_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
