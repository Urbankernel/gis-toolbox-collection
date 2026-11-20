# -*- coding: utf-8 -*-
"""BuslineTools —— 城市公交（地铁）线路/站点抓取工具箱（ArcGIS Pro Python Toolbox）。

薄入口：只做「参数定义 + 编排」，核心逻辑下沉到 busline_core 与 geo_common。

数据源：高德（/v3/bus/linename，REST，GCJ-02）；输出线路 LineString + 站点 Point 双图层（WGS-84）。
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
        _roots = [_os.path.join(_env, "BuslineTools"), _env]


class Toolbox:
    def __init__(self):
        self.label = "公交线路工具箱"
        self.alias = "BuslineTools"
        self.tools = [BuslineCrawler]


class BuslineCrawler:
    """按城市抓取公交/地铁线路与站点，输出线路（线）+ 站点（点）双图层。"""

    def __init__(self):
        self.label = "公交线路抓取"
        self.description = (
            "从高德地图按城市抓取公交（地铁）线路、站点与轨迹，"
            "支持指定线路 / 关键词 / 数字穷举三种检索模式，"
            "输出线路 LineString + 站点 Point 双图层（WGS-84，SHP / GPKG）。"
        )
        # 强制前台执行：Output 参数在后台会被替换成 scratch 临时路径，导致结果不落到指定路径。
        self.canRunInBackground = False

    # ------------------------------------------------------------------ 参数

    def getParameterInfo(self):
        params = []

        # ---- 检索 ----
        p = arcpy.Parameter(name="city", displayName="城市",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="api_keys", displayName="高德 Web 服务 Key（可多选）",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.multiValue = True
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="search_mode", displayName="检索模式",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.filter.list = ["lines", "only", "full"]
        p.value = "lines"
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="lines", displayName="指定线路（逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="only", displayName="关键词（only 模式）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="prefix", displayName="线路名前缀（逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="suffix", displayName="数字后缀（仅穷举，逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="extra", displayName="额外关键词（仅穷举，逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="max_num", displayName="穷举上限",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 1000
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="exact", displayName="精确检索（只留短名全等）",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.value = False
        p.category = "检索"
        params.append(p)

        # ---- 请求 ----
        p = arcpy.Parameter(name="max_qps", displayName="每秒请求数上限",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 28.0
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="delay", displayName="每次请求间隔（秒）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 0.0
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="offset", displayName="每页条数",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 20
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="timeout", displayName="请求超时（秒）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 15.0
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="retries", displayName="单请求最大重试",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 3
        p.category = "请求"
        params.append(p)

        # ---- 输出 ----
        p = arcpy.Parameter(name="output_lines", displayName="线路要素类（.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Required")
        p.value = ""
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="output_stops", displayName="站点要素类（.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Required")
        p.value = ""
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="output_crs", displayName="输出坐标系",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.value = "GCS_WGS_1984"
        p.category = "输出"
        params.append(p)

        return params

    # ------------------------------------------------------------------ 联动

    def updateParameters(self, parameters):
        mode = parameters[2].valueAsText
        if mode is None:
            return
        parameters[3].enabled = (mode == "lines")       # lines
        parameters[4].enabled = (mode == "only")        # only
        parameters[5].enabled = (mode in ("lines", "full"))  # prefix
        parameters[6].enabled = (mode == "full")        # suffix
        parameters[7].enabled = (mode == "full")        # extra
        parameters[8].enabled = (mode == "full")        # max_num
        return

    def updateMessages(self, parameters):
        mode = parameters[2].valueAsText
        if mode == "lines" and not (parameters[3].valueAsText and parameters[3].valueAsText.strip()):
            parameters[3].setWarningMessage("lines 模式请至少填一条线路，例如 406路,102路")
        if mode == "only" and not (parameters[4].valueAsText and parameters[4].valueAsText.strip()):
            parameters[4].setWarningMessage("only 模式请填写检索关键词，例如 机场")
        for idx in (15, 16):
            out = parameters[idx].valueAsText
            if out and out.strip() and not out.lower().endswith((".shp", ".gpkg")):
                parameters[idx].setErrorMessage("输出仅支持 .shp / .gpkg 扩展名")
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
                _roots = [_os.path.join(_env, "BuslineTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from geo_common.keypool import KeyPool
        from busline_core.client import AmapBusClient
        from busline_core.crawler import crawl
        from busline_core.keywords import build_keywords
        from busline_core.writer import write_busline

        # ---- 读参数 ----
        city = (parameters[0].valueAsText or "").strip()
        api_keys = [k.strip() for k in (parameters[1].valueAsText or "").split(";") if k.strip()]
        mode = (parameters[2].valueAsText or "lines").strip()
        lines = (parameters[3].valueAsText or "").strip() or None
        only = (parameters[4].valueAsText or "").strip() or None
        prefix = (parameters[5].valueAsText or "").strip() or None
        suffix = (parameters[6].valueAsText or "").strip() or None
        extra = (parameters[7].valueAsText or "").strip() or None
        max_num = int(parameters[8].value or 1000)
        exact = bool(parameters[9].value)

        max_qps = float(parameters[10].value or 28.0)
        delay = float(parameters[11].value or 0.0)
        offset = int(parameters[12].value or 20)
        timeout = float(parameters[13].value or 15.0)
        retries = int(parameters[14].value or 3)

        output_lines = parameters[15].valueAsText
        output_stops = parameters[16].valueAsText
        output_crs = (parameters[17].valueAsText or "GCS_WGS_1984").strip()

        # ---- 校验 ----
        if not city:
            raise ValueError("未提供城市名")
        if not api_keys:
            raise ValueError("未提供高德 Web 服务 Key")
        if not output_lines or not output_stops:
            raise ValueError("未指定线路/站点输出要素类")

        # ---- 组装客户端 ----
        pool = KeyPool(api_keys)
        client = AmapBusClient(pool, qps=max_qps, timeout=timeout,
                               retries=retries, delay=delay)

        # ---- 构造关键字队列 ----
        keywords = build_keywords(mode, lines=lines, only=only, prefix=prefix,
                                  suffix=suffix, extra=extra, max_num=max_num)
        arcpy.AddMessage(f"城市: {city}；模式: {mode}；关键字 {len(keywords)} 个")

        # ---- 抓取 ----
        def _progress(i, total, kw):
            arcpy.AddMessage(f"  [{i + 1}/{total}] 检索: {kw}")

        try:
            line_rows, stop_rows, stats = crawl(
                client, city, keywords, offset=offset, exact=exact,
                on_progress=_progress)
        except Exception as exc:  # 含 AllKeysExhausted 等
            raise ValueError(f"抓取失败：{exc}") from exc

        arcpy.AddMessage(
            f"抓取完成：线路 {stats['fetched_lines']} 条，站点 {stats['fetched_stops']} 个"
            + (f"，失败关键字 {len(stats['failed'])} 个" if stats["failed"] else ""))

        # ---- 落盘 ----
        sr = arcpy.SpatialReference(output_crs)
        write_busline(output_lines, output_stops, line_rows, stop_rows,
                      spatial_ref=sr)
        arcpy.AddMessage(f"线路: {output_lines}；站点: {output_stops}")

        # ---- 回设输出参数 ----
        arcpy.SetParameterAsText(15, output_lines)
        arcpy.SetParameterAsText(16, output_stops)
        return
