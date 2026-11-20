# gis-toolbox-collection

[![GitHub](https://img.shields.io/badge/GitHub-Urbankernel%2Fgis--toolbox--collection-2ea44f?logo=github&logoColor=white)](https://github.com/Urbankernel/gis-toolbox-collection)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

面向城市研究 / 规划 / GIS 从业者的 **ArcGIS Pro 工具箱合集**——把一批「在线地图与位置数据获取、空间分析」能力封装成**拖入即用**的 Python Toolbox（`.pyt`），无需写代码。

- **8 个工具箱**，覆盖 POI / AOI / 公交 / 街景 / 等时圈 / 路况 / 行政区边界 / 空气质量
- **统一三层架构**：薄 `.pyt` + 项目核心包 + 共享库 `geo_common`
- **统一坐标**：成果一律 WGS-84
- **零第三方依赖**（除 `shapely` 需手动安装，其余 arcpy 自带）

> ⚠️ 数据版权归各数据源方所有，部分工具走未公开接口。使用前请务必阅读 [免责声明与数据合规说明](DISCLAIMER.md)。

---

## 工具箱一览

| 分类 | 工具箱 | 功能 | 数据源 | 需 AK |
|---|---|---|---|---|
| 数据采集 | [AMapPOITools](AMapPOITools/README.md) | 高德 POI 批量抓取 | 高德 Web 服务 API | ✅ |
| 数据采集 | [BuslineTools](BuslineTools/README.md) | 高德公交线路 + 站点双图层 | 高德 REST API | ✅ |
| 数据采集 | [CnemcAirTools](CnemcAirTools/README.md) | 城市/点位空气质量（5 数据集） | 中国环境监测总站 | — |
| 边界与地理数据 | [ChinaBoundaryTools](ChinaBoundaryTools/README.md) | 国/省/市/区县四级边界 | DataV.GeoAtlas | — |
| 边界与地理数据 | [GetAOITools](GetAOITools/README.md) | 地名 → AOI 兴趣面 | 高德/百度隐藏接口 | ✅ |
| 空间分析 | [IsochroneTools](IsochroneTools/README.md) | 等时圈（等时带面 + 时间表面栅格） | 百度/腾讯 API | ✅ |
| 数据采集 | [TrafficScopeTools](TrafficScopeTools/README.md) | 实时路况 → 四级分类栅格 + 瓦片网格 | 百度/高德网页端瓦片 | — |
| 街景影像 | [StreetViewTools](StreetViewTools/README.md) | 百度街景下载（输出图片） | 百度全景逆向接口 | — |

---

## 环境要求

- **ArcGIS Pro**（Python Toolbox 依赖其内置的 arcpy 3.x / Python 3.11 环境）
- 额外依赖：`shapely`（在 ArcGIS Pro 的 Python 环境中执行 `pip install shapely`）
- 详细安装与自检见 [安装指南.md](安装指南.md)

## 快速上手

1. 安装 `shapely`（见上）；
2. 打开 ArcGIS Pro →「目录」窗格 →「工具箱」→ 右键「添加工具箱」；
3. 选择对应工具的 `.pyt` 文件（如 `AMapPOITools/AMapPOITools.pyt`）；
4. 按各工具 README 的参数说明填写并运行。

> 需要 AK 的工具，先在对应数据源官网申请 Key，填入工具参数即可。

## 目录结构

```
gis-toolbox-collection/
├── README.md                 # 本文件
├── LICENSE                   # MIT
├── DISCLAIMER.md             # 数据合规总免责
├── CHANGELOG.md              # 更新日志
├── CONTRIBUTING.md           # 贡献指南
├── 安装指南.md               # 环境准备与安装指南
├── geo_common/               # 8 工具共享库
├── AMapPOITools/  ...        # 每个工具箱：.pyt + *_core/ + README.md
└── scripts/                  # 环境自检脚本
```

## 合规声明

本仓库代码采用 **MIT 许可**；但各工具产出**数据的版权归数据源方**，部分工具使用未公开接口，**仅供学习研究，禁止商业使用与再分发**。完整说明见 [DISCLAIMER.md](DISCLAIMER.md)。

## 上游致谢

本仓库改造自 [Urbankernel](https://github.com/Urbankernel) 的开源项目：amap-poi-fetcher、get_aoi、busline-crawler、Baidu_StreetView_Download、Isochrone、TrafficScope、chinaboundary、cnemc-air。感谢上游作者的贡献。
