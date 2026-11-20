# -*- coding: utf-8 -*-
"""TrafficScopeTools —— 实时路况范围采集工具箱。

改造自 Urbankernel/TrafficScope（百度/高德网页端路况瓦片，无需 AK、无配额限制）。
两个工具：
  1. 采集实时路况 —— 范围面 → 切瓦片 → 采集 → 四级分类 → 镶嵌 → 栅格 + 瓦片网格
  2. 干跑估算 —— 零请求，预估瓦片数与耗时

坐标系纪律：成果一律 WGS-84。数据源为地图厂商未公开接口，仅限自用研究。
"""

import arcpy


class Toolbox(object):
    def __init__(self):
        self.label = "实时路况工具箱"
        self.alias = "TrafficScopeTools"
        self.tools = [TrafficCollector, TrafficEstimator]


# ------------------------------------------------------------------ 工具 1：采集实时路况

class TrafficCollector(object):
    def __init__(self):
        self.label = "采集实时路况"
        self.description = "按范围面采集百度/高德网页端实时路况瓦片，输出四级分类栅格 + 瓦片网格矢量"
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="scope_fc", displayName="范围面要素类",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Required")
        p.category = "输入"
        p.filter.list = ["Polygon"]
        params.append(p)

        p = arcpy.Parameter(name="provider", displayName="数据源",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "采集"
        p.filter.list = ["amap", "baidu"]
        p.value = "amap"
        params.append(p)

        p = arcpy.Parameter(name="level", displayName="瓦片级别（auto 或 10-18 整数）",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "采集"
        p.value = "auto"
        params.append(p)

        p = arcpy.Parameter(name="qps", displayName="QPS（每秒请求数）",
                            direction="Input", datatype="GPDouble", parameterType="Required")
        p.category = "采集"
        p.value = 5.0
        params.append(p)

        p = arcpy.Parameter(name="max_retries", displayName="最大重试次数",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.category = "采集"
        p.value = 3
        params.append(p)

        p = arcpy.Parameter(name="clip", displayName="裁剪到范围",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.category = "采集"
        p.value = True
        params.append(p)

        p = arcpy.Parameter(name="out_tif", displayName="输出路况栅格（.tif）",
                            direction="Output", datatype="DERasterDataset", parameterType="Required")
        p.category = "输出"
        p.value = ""
        params.append(p)

        p = arcpy.Parameter(name="out_tiles", displayName="输出瓦片网格（.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Required")
        p.category = "输出"
        p.value = ""
        params.append(p)

        p = arcpy.Parameter(name="work_dir", displayName="工作目录（断点续跑，可选）",
                            direction="Input", datatype="DEFolder", parameterType="Optional")
        p.category = "断点续跑"
        params.append(p)

        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        import os as _os
        import sys as _sys
        _roots = []
        try:
            _here = _os.path.dirname(_os.path.abspath(__file__))
            _roots = [_here, _os.path.dirname(_here)]
        except NameError:
            _env = _os.environ.get("GIS_TOOLBOX_ROOT")
            if _env:
                _roots = [_os.path.join(_env, "TrafficScopeTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from geo_common.params import resolve_feature_class
        from traffic_scope_core.pipeline import run_pipeline
        from traffic_scope_core.scope import load_area_from_feature_class

        scope_fc = resolve_feature_class(parameters[0].value)
        provider = (parameters[1].valueAsText or "amap").strip().lower()
        level = (parameters[2].valueAsText or "auto").strip()
        qps = float(parameters[3].value or 5.0)
        max_retries = int(parameters[4].value or 3)
        clip = bool(parameters[5].value)
        out_tif = parameters[6].valueAsText
        out_tiles = parameters[7].valueAsText
        work_dir = parameters[8].valueAsText or None

        scope = load_area_from_feature_class(scope_fc)
        run_pipeline(
            scope=scope,
            provider=provider,
            level=level,
            qps=qps,
            max_retries=max_retries,
            clip=clip,
            out_tif=out_tif,
            out_tiles=out_tiles,
            work_dir=work_dir,
            log=messages.AddMessage,
        )
        return


# ------------------------------------------------------------------ 工具 2：干跑估算

class TrafficEstimator(object):
    def __init__(self):
        self.label = "干跑估算"
        self.description = "零请求，预估瓦片数与预计耗时"
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="scope_fc", displayName="范围面要素类",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Required")
        p.category = "输入"
        p.filter.list = ["Polygon"]
        params.append(p)

        p = arcpy.Parameter(name="provider", displayName="数据源",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "估算"
        p.filter.list = ["amap", "baidu"]
        p.value = "amap"
        params.append(p)

        p = arcpy.Parameter(name="level", displayName="瓦片级别（auto 或 10-18 整数）",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "估算"
        p.value = "auto"
        params.append(p)

        p = arcpy.Parameter(name="qps", displayName="QPS（每秒请求数）",
                            direction="Input", datatype="GPDouble", parameterType="Required")
        p.category = "估算"
        p.value = 5.0
        params.append(p)

        return params

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        import os as _os
        import sys as _sys
        _roots = []
        try:
            _here = _os.path.dirname(_os.path.abspath(__file__))
            _roots = [_here, _os.path.dirname(_here)]
        except NameError:
            _env = _os.environ.get("GIS_TOOLBOX_ROOT")
            if _env:
                _roots = [_os.path.join(_env, "TrafficScopeTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from geo_common.params import resolve_feature_class
        from traffic_scope_core.estimate import estimate
        from traffic_scope_core.scope import load_area_from_feature_class

        scope_fc = resolve_feature_class(parameters[0].value)
        provider = (parameters[1].valueAsText or "amap").strip().lower()
        level = (parameters[2].valueAsText or "auto").strip()
        qps = float(parameters[3].value or 5.0)

        scope = load_area_from_feature_class(scope_fc)
        n, est_seconds, lv, res = estimate(scope, provider, level, qps)

        messages.AddMessage("===== 干跑估算（零请求）=====")
        messages.AddMessage(f"范围: {scope.name}，面积约 {scope.area_km2():.1f} km²")
        messages.AddMessage(f"路况瓦片数: {n}（provider={provider}，level={lv}，"
                            f"约 {res:.0f} m/px，无需 AK、无配额限制）")
        messages.AddMessage(f"预计耗时: 约 {est_seconds:.0f} 秒（QPS={qps}，保守限速）")
        if est_seconds > 600:
            messages.AddMessage("提示：范围大且级别高，瓦片数较多；建议降低 level 或先小范围验证链路。")
        return
