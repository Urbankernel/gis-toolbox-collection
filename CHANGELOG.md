# 更新日志

> 仓库地址：<https://github.com/Urbankernel/gis-toolbox-collection>

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)（`v主.次.补丁`）。

## [1.0.0] - 2026-11-20

### 新增
- 首次公开发布：8 个 ArcGIS Pro 工具箱合集。
- AMapPOITools、GetAOITools、BuslineTools、StreetViewTools、IsochroneTools、TrafficScopeTools、ChinaBoundaryTools、CnemcAirTools。

### 变更
- 清除全部 `.pyt` 中的硬编码绝对路径，改为 `__file__` 相对推导 + `GIS_TOOLBOX_ROOT` 环境变量兜底。
- 补充 `LICENSE`（MIT）、`DISCLAIMER.md`、总 `README.md`、`.gitignore`。
- 每个工具箱补充统一模板的 `README.md`。
