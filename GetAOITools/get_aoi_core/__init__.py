# -*- coding: utf-8 -*-
"""get_aoi_core —— 地名 → AOI 兴趣面边界 项目核心包。

与原开源项目 get_aoi 对应，职责：
- models: POIInput / Candidate / AOIRecord / Status 数据结构
- text: 名称标准化（NFKC / 去标点 / 忽略大小写）
- geometry: GeoJSON 几何构建、近似面积、中心点、距离
- filter: 候选过滤（分类/父子/名称相关性/去重）与结果取舍
- matcher: 四维加权综合排序取最优
- providers: 高德 / 百度两段式 Provider（search + fetch_boundary）
- http: 带随机 UA、反爬检测、限流的 HTTP 客户端
- pipeline: 清单 → 检索 → 过滤 → 抠边界 → 取舍 编排
- writer: arcpy 写 AOI 面要素类（复用 geo_common.exporter.write_polygons）

纯逻辑模块（models/text/geometry/filter/matcher）无 arcpy/网络依赖，可 pytest。
"""
