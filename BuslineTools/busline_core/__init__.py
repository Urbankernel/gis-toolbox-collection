# -*- coding: utf-8 -*-
"""busline_core —— 城市公交（地铁）线路/站点抓取核心包（高德数据源）。

薄 .pyt + 核心包三层架构的项目核心层：纯逻辑模块（client/parser/keywords/crawler）
不依赖 arcpy / shapely，可命令行 pytest；writer 负责 arcpy 落盘。
"""
__version__ = "1.0.0"
