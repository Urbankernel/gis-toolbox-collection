# 贡献指南

感谢你对 [gis-toolbox-collection](https://github.com/Urbankernel/gis-toolbox-collection) 的关注！本项目是 ArcGIS Pro 工具箱合集，欢迎提交 issue 与 PR。

## 报告问题

请使用 issue 报告以下类型的问题，并尽量提供：

1. **接口失效**：数据源接口返回异常、前端改版导致抓取失败（请附报错信息、数据源名称、最后可用的日期）；
2. **环境报错**：在 ArcGIS Pro 中运行报错（请附完整 Traceback、ArcGIS Pro 版本、是否已装 `shapely`）；
3. **功能建议**：描述场景与期望。

## 提交 PR

1. 保持与现有架构一致：**薄 `.pyt` + 项目核心包 + 共享库 `geo_common`**；
2. 遵循已有的 arcpy 3.x 兼容性约束（详见各工具源码与 [安装指南.md](安装指南.md)）；
3. 改动后请运行 `python scripts/verify_env.py` 与语法检查（`python -m py_compile`）；
4. 涉及新数据源或接口时，请同步更新**对应工具箱目录下 README** 的「数据源与许可」小节与 [DISCLAIMER.md](DISCLAIMER.md)。

## 代码规范

- 统一使用 UTF-8 编码、中文注释；
- 坐标成果统一 WGS-84；
- 不在代码中硬编码绝对路径或密钥。

感谢贡献！
