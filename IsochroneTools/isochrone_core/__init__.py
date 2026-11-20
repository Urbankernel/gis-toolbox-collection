"""isochrone_core —— 等时圈 / 可达性分析核心包。

从 Urbankernel/Isochrone 改造而来，沿用「薄 .pyt + 核心包 + geo_common」架构。

核心链路：
    出发点(点要素) + 研究区(面要素)
        → 渔网终点生成 (grid.py)
        → 批量算路 (client.py 百度 / tencent.py 腾讯)
        → IDW 插值时间表面 (interpolate.py)
        → 等值带提取等时带面 (interpolate.py)
        → 矢量(geo_common.exporter) + 栅格(arcpy.NumPyArrayToRaster) 落盘 (writer.py)

关键改造点（相对原项目）：
- aiohttp 异步请求 → requests 同步 + geo_common.ratelimit 限速；
- rasterio 写栅格 → arcpy.NumPyArrayToRaster；
- geopandas 矢量导出 → geo_common.exporter；
- affine.Affine → 6 元组 geotransform；
- pyproj 米↔度换算 → 球面近似；
- 多 key 轮换 → geo_common.keypool（三态）。

坐标系纪律：请求链路按引擎转 GCJ-02（腾讯）/ WGS-84（百度 coord_type=wgs84），
成果层一律 WGS-84。
"""
