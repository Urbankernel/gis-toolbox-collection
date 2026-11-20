"""amap_poi_core —— 高德 POI 抓取的项目特有核心逻辑。

依赖 geo_common（坐标转换 / key 轮换 / 限流 / 渔网 / 导出），
只保留高德 place/polygon 接口特有部分：
- client    高德 API 封装（分页 + key 轮换 + 退避）
- parser    POI 响应解析（37 字段展平）与字段定义
- scope     4 种范围输入 → WGS-84 几何
- scheduler 递归四分调度 + 去重 + 范围过滤
"""

__all__ = ["client", "parser", "scope", "scheduler"]
