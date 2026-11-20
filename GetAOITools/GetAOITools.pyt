# -*- coding: utf-8 -*-
"""GetAOITools —— 地名清单 → AOI 兴趣面边界 工具箱（ArcGIS Pro Python Toolbox）。

薄入口：只做「参数定义 + 编排」，核心逻辑下沉到 get_aoi_core 与 geo_common。

流程：地名清单 → 检索（官方接口）→ 候选过滤 → 名称相关性 → 取舍 →
抠边界（网页隐藏接口）→ 坐标转换 → 输出 AOI 面（WGS-84，SHP / GPKG）。

数据源：高德（place/text + getPoiInfo，GCJ-02）或 百度（place/v2/search + qt=ext，bd09mc）。
"""
import os
import sys

import arcpy

# 前台 / 命令行兜底（ArcGIS Pro 后台执行时 execute 跑独立子进程，真正保险在 execute 内）
_THIS_DIR = None
try:
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    _PARENT_DIR = os.path.dirname(_THIS_DIR)
    for _d in (_THIS_DIR, _PARENT_DIR):
        if _d and _d not in sys.path:
            sys.path.insert(0, _d)
except NameError:
    _env = _os.environ.get("GIS_TOOLBOX_ROOT")
    if _env:
        _roots = [_os.path.join(_env, "GetAOITools"), _env]


class Toolbox:
    def __init__(self):
        self.label = "名称转 AOI 面工具箱"
        self.alias = "GetAOITools"
        self.tools = [NameToAoi]


class NameToAoi:
    """给定地名清单，自动检索、过滤、匹配并提取高德/百度 AOI 面边界，输出 SHP / GPKG。"""

    def __init__(self):
        self.label = "名称转 AOI 面"
        self.description = (
            "给一份地名清单，自动完成「名称 → 检索 → 候选过滤 → 综合排序 → 抠边界 → 坐标转换」，"
            "输出每个地名对应的 AOI 兴趣面（WGS-84，SHP / GPKG）。支持高德、百度两个数据源。"
        )
        # 强制前台执行：Output 参数在后台会被替换成 scratch 临时路径，导致结果不落到指定路径；
        # 前台执行保证路径原样传入 + AddMessage 进度实时可见。
        self.canRunInBackground = False

    # ------------------------------------------------------------------ 参数

    def getParameterInfo(self):
        params = []

        # ---- 输入 ----
        p = arcpy.Parameter(name="names_csv", displayName="地名清单 CSV",
                            direction="Input", datatype="DEFile", parameterType="Optional")
        p.filter.list = ["csv"]
        p.category = "输入"
        params.append(p)

        p = arcpy.Parameter(name="names_text", displayName="地名清单（TXT 文件）",
                            direction="Input", datatype="DEFile", parameterType="Optional")
        p.filter.list = ["txt"]
        p.category = "输入"
        params.append(p)

        # ---- 数据源 ----
        p = arcpy.Parameter(name="provider", displayName="数据源",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.filter.list = ["amap", "baidu"]
        p.value = "amap"
        p.category = "数据源"
        params.append(p)

        p = arcpy.Parameter(name="api_keys", displayName="高德 Key / 百度 AK（可多选）",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.multiValue = True
        p.category = "数据源"
        params.append(p)

        p = arcpy.Parameter(name="city", displayName="全局城市（缩小检索范围）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "数据源"
        params.append(p)

        # ---- 候选处理 ----
        p = arcpy.Parameter(name="want", displayName="多结果取舍",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.filter.list = ["all", "main", "keyword"]
        p.value = "all"
        p.category = "候选处理"
        params.append(p)

        p = arcpy.Parameter(name="keyword", displayName="分支关键词（want=keyword 时）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "候选处理"
        params.append(p)

        p = arcpy.Parameter(name="keep_types", displayName="分类白名单（逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "候选处理"
        params.append(p)

        p = arcpy.Parameter(name="min_area", displayName="最小面积（km²）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 0.0
        p.category = "候选处理"
        params.append(p)

        p = arcpy.Parameter(name="max_area", displayName="最大面积（km²）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 1000000.0
        p.category = "候选处理"
        params.append(p)

        p = arcpy.Parameter(name="max_candidates", displayName="每地名最大候选数",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 25
        p.category = "候选处理"
        params.append(p)

        # ---- 请求 ----
        p = arcpy.Parameter(name="min_interval", displayName="请求最小间隔（秒）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 3.0
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="max_interval", displayName="请求最大间隔（秒）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 10.0
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="retries", displayName="单请求最大重试",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 3
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="timeout", displayName="请求超时（秒）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 15.0
        p.category = "请求"
        params.append(p)

        # ---- 输出 ----
        p = arcpy.Parameter(name="output_features", displayName="输出 AOI 面要素类（.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Required")
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="output_crs", displayName="输出坐标系",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.value = "GCS_WGS_1984"
        p.category = "输出"
        params.append(p)

        # ---- 高级（高德边界接口反爬兜底）----
        p = arcpy.Parameter(name="boundary_cookie", displayName="边界接口 Cookie（可选）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "高级"
        params.append(p)

        p = arcpy.Parameter(name="boundary_ua", displayName="边界接口 User-Agent（可选）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "高级"
        params.append(p)

        return params

    # ------------------------------------------------------------------ 联动

    def updateParameters(self, parameters):
        want = parameters[5].valueAsText
        if want is not None:
            parameters[6].enabled = (want == "keyword")
        return

    def updateMessages(self, parameters):
        # 输入二选一：CSV 或 多行文本至少填一个
        has_csv = bool(parameters[0].valueAsText and parameters[0].valueAsText.strip())
        has_txt = bool(parameters[1].valueAsText and parameters[1].valueAsText.strip())
        if not has_csv and not has_txt:
            parameters[0].setWarningMessage("请提供地名清单 CSV，或选择地名清单 TXT 文件（每行一个地名）")
        # 输出扩展名校验
        out = parameters[15].valueAsText
        if out and out.strip() and not out.lower().endswith((".shp", ".gpkg")):
            parameters[15].setErrorMessage("输出仅支持 .shp / .gpkg 扩展名")
        return

    # ------------------------------------------------------------------ 执行

    def execute(self, parameters, messages):
        # ---- 路径注入（关键）：后台子进程不继承模块级 sys.path，必须在 execute 内重做 ----
        import os as _os
        import sys as _sys
        _roots = []
        try:
            _here = _os.path.dirname(_os.path.abspath(__file__))
            _roots = [_here, _os.path.dirname(_here)]
        except NameError:
            _env = _os.environ.get("GIS_TOOLBOX_ROOT")
            if _env:
                _roots = [_os.path.join(_env, "GetAOITools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from get_aoi_core.pipeline import (make_provider, parse_names_csv,
                                           parse_names_txt, process_names)
        from get_aoi_core.writer import write_aoi_records

        # ---- 读参数 ----
        names_csv = parameters[0].valueAsText
        names_txt = parameters[1].valueAsText
        provider = (parameters[2].valueAsText or "amap").strip()
        api_keys = [k.strip() for k in (parameters[3].valueAsText or "").split(";") if k.strip()]
        city = (parameters[4].valueAsText or "").strip() or None
        want = (parameters[5].valueAsText or "all").strip()
        keyword = (parameters[6].valueAsText or "").strip() or None
        keep_types_raw = (parameters[7].valueAsText or "").strip()
        min_area = float(parameters[8].value or 0.0)
        max_area = float(parameters[9].value or 1000000.0)
        max_candidates = int(parameters[10].value or 25)
        min_interval = float(parameters[11].value or 3.0)
        max_interval = float(parameters[12].value or 10.0)
        retries = int(parameters[13].value or 3)
        timeout = float(parameters[14].value or 15.0)
        output_features = parameters[15].valueAsText
        output_crs = (parameters[16].valueAsText or "GCS_WGS_1984").strip()
        boundary_cookie = (parameters[17].valueAsText or "").strip() or None
        boundary_ua = (parameters[18].valueAsText or "").strip() or None

        # ---- 校验 ----
        if not api_keys:
            raise ValueError("未提供数据源 Key（高德 Web 服务 Key / 百度服务端 AK）")
        if not output_features:
            raise ValueError("未指定输出要素类")

        # ---- 解析地名清单 ----
        names = []
        if names_csv and names_csv.strip():
            names = parse_names_csv(names_csv)
        elif names_txt and names_txt.strip():
            names = parse_names_txt(names_txt, city=city)
        if not names:
            raise ValueError("地名清单为空：请提供 CSV 或 TXT 文件")

        keep_types = [t.strip() for t in keep_types_raw.split(",") if t.strip()] or None

        # ---- 组装 Provider ----
        api_key = api_keys[0]
        if len(api_keys) > 1:
            arcpy.AddMessage(f"提示：已提供 {len(api_keys)} 个 Key，本次使用第一个（{api_key[:8]}...）")
        provider_obj = make_provider(
            provider, api_key, max_candidates=max_candidates,
            min_interval=min_interval, max_interval=max_interval,
            retries=retries, timeout=timeout,
            boundary_cookie=boundary_cookie, boundary_ua=boundary_ua)

        # ---- 输出坐标系 ----
        sr = arcpy.SpatialReference(output_crs)
        arcpy.AddMessage(
            f"数据源: {provider}；地名 {len(names)} 个；输出: {output_features}")

        # ---- 调度抓取 ----
        def _progress(i, total, name):
            arcpy.AddMessage(f"  [{i + 1}/{total}] 处理: {name}")

        records, summary = process_names(
            provider_obj, names, want=want, keyword=keyword,
            keep_types=keep_types, min_area=min_area, max_area=max_area,
            on_progress=_progress)

        # ---- 落盘 AOI 面 ----
        if not records:
            arcpy.AddWarning("未匹配到任何 AOI 面，不生成输出文件")
        else:
            fc = write_aoi_records(output_features, records, spatial_ref=sr)
            arcpy.AddMessage(f"AOI 面: {fc}")

        # ---- 汇报 ----
        matched = sum(1 for s in summary if s["status"] == "Matched")
        no_cand = sum(1 for s in summary if s["status"] == "No Candidate")
        no_geom = sum(1 for s in summary if s["status"] == "No Geometry")
        arcpy.AddMessage(
            f"完成：{len(names)} 个地名，成功 {matched}，无候选 {no_cand}，无边界 {no_geom}；"
            f"输出 AOI 面 {len(records)} 个")

        arcpy.SetParameterAsText(15, output_features)
        return
