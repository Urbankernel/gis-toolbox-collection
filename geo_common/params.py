# -*- coding: utf-8 -*-
"""参数归一化工具：把 arcpy 参数值统一转成「可被游标使用的数据源路径字符串」。

问题背景：
    arcpy 中「要素类」类的 Input 参数，若 datatype 用 `DEFeatureClass`，只接受磁盘上的
    数据集路径（浏览按钮 / Catalog 拖数据文件）；从地图「内容」窗格拖入图层时，ArcGIS Pro
    只会塞入图层名（如 `roads`），并给参数标红——因为图层名不是有效的数据源路径。

    正确做法是：**输入要素类参数一律用 `DEFeatureLayer`（要素图层）**，这样三种喂法都能
    被接受：
    - 从地图内容窗格拖入图层；
    - 浏览按钮选磁盘数据；
    - Catalog 拖数据文件。
    此时 `parameter.value` 返回的是 Layer 对象（而非路径字符串），需要用本模块的
    `resolve_feature_class` 从 Layer 对象中提取磁盘数据源路径，再交给
    `arcpy.da.SearchCursor(...)` 等游标使用。

约定：arcpy 延迟导入，本模块顶层零 arcpy 依赖，命令行可 import。
"""

from __future__ import annotations

from typing import Any


def resolve_feature_class(value: Any) -> str | None:
    """把「要素图层」参数值归一化为磁盘数据源路径字符串。

    参数:
        value: `arcpy.Parameter.value`（可能是 str / Layer 对象 / None）。

    返回:
        数据源路径字符串；无法解析时返回 None。

    解析顺序：
        1. None → None；
        2. 已是字符串 → 原样返回（防御：DEFeatureClass 遗留 / 直接传路径场景）；
        3. Layer 对象 → 优先 `Describe(value).catalogPath`（实测可靠），
           为空或异常时回退 `.dataSource` 属性。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value

    # Layer 对象（DEFeatureLayer 参数从地图内容窗格拖入的图层）
    try:
        import arcpy

        cp = arcpy.Describe(value).catalogPath
        if isinstance(cp, str) and cp:
            return cp
    except Exception:
        pass

    ds = getattr(value, "dataSource", None)
    if isinstance(ds, str) and ds:
        return ds
    return None
