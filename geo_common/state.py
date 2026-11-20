"""断点续跑状态（JSON 读写）。

状态文件采用「先写临时文件再原子替换」避免中断写出半个文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RunState:
    """单次运行的进度状态管理。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, state: dict[str, Any]) -> None:
        """原子写状态文件。"""
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        tmp.replace(self.path)

    def load(self) -> dict[str, Any] | None:
        """读状态文件；不存在返回 None。"""
        if not self.path.exists():
            return None
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
