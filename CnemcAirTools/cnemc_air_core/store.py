"""断点续跑状态管理（精简自 cnemc-air 的 store.py，砍掉 YAML 快照）。

工具箱场景下成果直接写到用户指定的输出目录，不建时间戳子目录；
progress.json（城市名 → done/failed）落输出目录，重跑时跳过已完成城市。
"""

from __future__ import annotations

import json
import os
from typing import Any


def load_progress(path: str) -> dict[str, str]:
    """读取 progress.json（城市名 → done/failed）；不存在返回空 dict。"""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    return {}


def save_progress(path: str, progress: dict[str, str]) -> None:
    """写入 progress.json（城市名 → done/failed）。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def save_raw(out_dir: str, name: str, obj: Any) -> str:
    """保存一份原始接口响应（JSON，审计/字段漂移追溯），返回路径。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path
