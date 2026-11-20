"""geo_common —— 共享公共库。

被 Urbankernel 名下多个 ArcGIS 工具箱改造项目共用的底层能力，
与具体数据源（高德/百度/腾讯）解耦，也不含任何业务编排。

模块一览：
- coords     坐标系互转（GCJ-02 / BD-09 / WGS-84），覆盖高德 + 百度
- keypool    多 key 三态轮换（ACTIVE / COOLDOWN / DEAD）
- ratelimit  QPS 限流 + 指数退避重试
- fishnet    渔网 / 瓦片网格划分与四分裂
- state      断点续跑状态（JSON 读写）
- exporter   arcpy 直写 SHP / GPKG（依赖 arcpy，按需延迟导入）

依赖纪律：除 exporter 外，其余模块零第三方 / 零 arcpy 依赖，
可在命令行用 pytest 独立验证。
"""

__version__ = "0.1.0"

__all__ = [
    "coords",
    "keypool",
    "ratelimit",
    "fishnet",
    "state",
    "exporter",
]
