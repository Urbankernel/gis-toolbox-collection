# -*- coding: utf-8 -*-
"""IsochroneTools —— 沿真实路网的城市等时圈 / 可达性分析工具箱。

改造自 Urbankernel/Isochrone（百度批量算路 + 腾讯距离矩阵双引擎）。
两个工具：
  1. 生成等时圈 —— 完整流水线：渔网终点 → 批量算路 → IDW 插值 → 等值带提取
  2. 干跑估算 —— 零配额消耗，预估终点数 / 请求次数 / 配额占用

坐标系纪律：请求链路按引擎转换，成果一律 WGS-84。
"""

import arcpy


class Toolbox(object):
    def __init__(self):
        self.label = "等时圈工具箱"
        self.alias = "IsochroneTools"
        self.tools = [IsochroneGenerator, IsochroneEstimator]


# ------------------------------------------------------------------ 工具 1：生成等时圈

class IsochroneGenerator(object):
    def __init__(self):
        self.label = "生成等时圈"
        self.description = "输入出发点与研究区，沿真实路网计算等时圈（等时带面 + 时间表面栅格）"
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="origins_fc", displayName="出发点要素类（点）",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Required")
        p.category = "输入"
        p.filter.list = ["Point"]
        params.append(p)

        p = arcpy.Parameter(name="area_fc", displayName="研究区面要素类",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Required")
        p.category = "输入"
        p.filter.list = ["Polygon"]
        params.append(p)

        p = arcpy.Parameter(name="engine", displayName="算路引擎",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "引擎"
        p.filter.list = ["baidu", "tencent"]
        p.value = "baidu"
        params.append(p)

        p = arcpy.Parameter(name="trans_type", displayName="出行方式",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "引擎"
        p.filter.list = ["driving", "walking", "riding"]
        p.value = "driving"
        params.append(p)

        p = arcpy.Parameter(name="tactics", displayName="驾车策略（仅百度驾车）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "引擎"
        p.filter.list = ["11", "10", "12", "13"]
        p.value = "11"
        params.append(p)

        p = arcpy.Parameter(name="thresholds", displayName="等时带阈值（分钟，逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "插值"
        p.value = "5,10,15,30"
        params.append(p)

        p = arcpy.Parameter(name="cell_size", displayName="渔网间距（米）",
                            direction="Input", datatype="GPLong", parameterType="Required")
        p.category = "插值"
        p.value = 300
        params.append(p)

        p = arcpy.Parameter(name="raster_cell", displayName="栅格分辨率（米）",
                            direction="Input", datatype="GPLong", parameterType="Required")
        p.category = "插值"
        p.value = 50
        params.append(p)

        p = arcpy.Parameter(name="baidu_ak", displayName="百度 AK（多 key 用分号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "凭证"
        params.append(p)

        p = arcpy.Parameter(name="tencent_key", displayName="腾讯 key（多 key 用分号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "凭证"
        params.append(p)

        p = arcpy.Parameter(name="qps", displayName="QPS（每秒请求数）",
                            direction="Input", datatype="GPDouble", parameterType="Required")
        p.category = "凭证"
        p.value = 3.0
        params.append(p)

        p = arcpy.Parameter(name="out_isochrone", displayName="输出等时带面（.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Required")
        p.category = "输出"
        p.value = ""
        params.append(p)

        p = arcpy.Parameter(name="out_raster", displayName="输出时间表面栅格（.tif）",
                            direction="Output", datatype="DERasterDataset", parameterType="Required")
        p.category = "输出"
        p.value = ""
        params.append(p)

        p = arcpy.Parameter(name="out_fishnet", displayName="输出渔网终点（可选，.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Optional")
        p.category = "输出"
        p.value = ""
        params.append(p)

        p = arcpy.Parameter(name="out_unreachable", displayName="输出不可达点（可选，.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Optional")
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
                _roots = [_os.path.join(_env, "IsochroneTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from geo_common.params import resolve_feature_class
        from isochrone_core.pipeline import run_isochrone

        origins_fc = resolve_feature_class(parameters[0].value)
        area_fc = resolve_feature_class(parameters[1].value)
        engine = (parameters[2].valueAsText or "baidu").strip()
        trans_type = (parameters[3].valueAsText or "driving").strip()
        tactics = (parameters[4].valueAsText or "11").strip()
        thresholds = _parse_thresholds(parameters[5].valueAsText)
        cell_size = int(parameters[6].value or 300)
        raster_cell = int(parameters[7].value or 50)
        baidu_ak = parameters[8].valueAsText
        tencent_key = parameters[9].valueAsText
        qps = float(parameters[10].value or 3.0)
        out_isochrone = parameters[11].valueAsText
        out_raster = parameters[12].valueAsText
        out_fishnet = parameters[13].valueAsText or None
        out_unreachable = parameters[14].valueAsText or None
        work_dir = parameters[15].valueAsText or None

        # 按引擎取对应 key
        aks = (tencent_key if engine == "tencent" else baidu_ak)
        if not aks:
            key_name = "腾讯 key" if engine == "tencent" else "百度 AK"
            raise ValueError(f"引擎选择 {engine}，但未提供 {key_name}")

        stats = run_isochrone(
            origins_fc=origins_fc,
            area_fc=area_fc,
            engine=engine,
            trans_type=trans_type,
            tactics=tactics,
            thresholds=thresholds,
            cell_size=cell_size,
            raster_cell=raster_cell,
            aks=aks,
            qps=qps,
            max_retries=3,
            out_isochrone=out_isochrone,
            out_raster=out_raster,
            out_fishnet=out_fishnet,
            out_unreachable=out_unreachable,
            work_dir=work_dir,
            messages=messages,
        )

        messages.AddMessage(
            f"完成：出发点 {stats['n_origins']} 个，渔网终点 {stats['n_points']} 个，"
            f"可达 {stats['n_reachable']} / 不可达 {stats['n_unreachable']}，"
            f"等时带 {stats['n_bands']} 条")
        return


# ------------------------------------------------------------------ 工具 2：干跑估算

class IsochroneEstimator(object):
    def __init__(self):
        self.label = "干跑估算"
        self.description = "零配额消耗，预估渔网终点数 / 请求次数 / 预计耗时 / 日配额占用"
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="origins_fc", displayName="出发点要素类（点）",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Required")
        p.category = "输入"
        p.filter.list = ["Point"]
        params.append(p)

        p = arcpy.Parameter(name="area_fc", displayName="研究区面要素类",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Required")
        p.category = "输入"
        p.filter.list = ["Polygon"]
        params.append(p)

        p = arcpy.Parameter(name="engine", displayName="算路引擎",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "估算"
        p.filter.list = ["baidu", "tencent"]
        p.value = "baidu"
        params.append(p)

        p = arcpy.Parameter(name="cell_size", displayName="渔网间距（米）",
                            direction="Input", datatype="GPLong", parameterType="Required")
        p.category = "估算"
        p.value = 300
        params.append(p)

        p = arcpy.Parameter(name="qps", displayName="QPS（每秒请求数）",
                            direction="Input", datatype="GPDouble", parameterType="Required")
        p.category = "估算"
        p.value = 3.0
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
                _roots = [_os.path.join(_env, "IsochroneTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from geo_common.params import resolve_feature_class
        from isochrone_core.grid import make_fishnet
        from isochrone_core.quota import dry_run_estimate
        from isochrone_core.study_area import read_origins, read_polygon

        origins_fc = resolve_feature_class(parameters[0].value)
        area_fc = resolve_feature_class(parameters[1].value)
        engine = (parameters[2].valueAsText or "baidu").strip()
        cell_size = int(parameters[3].value or 300)
        qps = float(parameters[4].value or 3.0)

        origins = read_origins(origins_fc)
        polygon = read_polygon(area_fc)
        points = make_fishnet(polygon, cell_size)

        if engine == "tencent":
            from isochrone_core.quota import TENCENT_DAILY_QUOTA, TENCENT_CONCURRENCY_LIMIT
            est = dry_run_estimate(
                n_points=len(points), n_origins=len(origins), qps=qps,
                origins_per_request=1, dests_per_request=5,
                daily_quota=TENCENT_DAILY_QUOTA,
                concurrency_limit=TENCENT_CONCURRENCY_LIMIT, provider="腾讯")
        else:
            est = dry_run_estimate(
                n_points=len(points), n_origins=len(origins), qps=qps,
                provider="百度")

        messages.AddMessage("===== 干跑估算（零配额消耗）=====")
        messages.AddMessage(f"出发点：{est.n_origins} 个")
        messages.AddMessage(f"渔网终点：{est.n_points} 个")
        messages.AddMessage(f"HTTP 请求次数：{est.n_requests} 次")
        messages.AddMessage(f"路线数（配额口径）：{est.n_pairs} 条")
        messages.AddMessage(f"预计耗时：约 {est.est_seconds:.0f} 秒（QPS={qps}）")
        messages.AddMessage(f"并发峰值：约 {est.peak_pairs_per_sec:.0f} 条/秒")
        if est.note:
            messages.AddMessage(f"说明：{est.note}")
        return


def _parse_thresholds(text):
    """解析逗号分隔的阈值字符串为升序 float 列表。"""
    if not text:
        return [5.0, 10.0, 15.0, 30.0]
    vals = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = float(part)
        except ValueError:
            continue
        if v > 0 and v not in vals:
            vals.append(v)
    vals.sort()
    return vals or [5.0, 10.0, 15.0, 30.0]
