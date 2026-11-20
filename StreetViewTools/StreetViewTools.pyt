# -*- coding: utf-8 -*-
"""StreetViewTools —— 百度街景批量下载工具箱（ArcGIS Pro Python Toolbox）。

薄入口：只做「参数定义 + 编排」，核心逻辑下沉到 streetview_core 与 geo_common。

数据源：百度全景逆向接口（qt=qsdata/pdata/sdata，无需 AK）。
流程：本地路网 → [工具1 生成采样点] → [工具2 批量下载街景] → 街景 JPG + 下载报告点要素类。
坐标纪律：成果一律 WGS84；BD09MC 仅用于发请求（geo_common.coords 内转换）。
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
        _roots = [_os.path.join(_env, "StreetViewTools"), _env]


class Toolbox:
    def __init__(self):
        self.label = "百度街景工具箱"
        self.alias = "StreetViewTools"
        self.tools = [SamplePoints, DownloadStreetView]


def _inject_paths():
    """把项目根 / 工具目录注入 sys.path（后台子进程必需，前台/命令行兜底）。"""
    import os as _os
    import sys as _sys

    _roots = []
    try:
        _here = _os.path.dirname(_os.path.abspath(__file__))
        _roots = [_here, _os.path.dirname(_here)]
    except NameError:
        _env = _os.environ.get("GIS_TOOLBOX_ROOT")
        if _env:
            _roots = [_os.path.join(_env, "StreetViewTools"), _env]
    for _d in _roots:
        if _d and _d not in _sys.path:
            _sys.path.insert(0, _d)


class SamplePoints:
    """工具 1：从本地路网要素类生成采样点（沿路等间距插值 + 计算街道走向 bearing）。"""

    def __init__(self):
        self.label = "生成采样点"
        self.description = (
            "从本地路网线要素类沿路等间距生成采样点，计算街道走向方位角 bearing，"
            "输出 WGS84 采样点点要素类（供「批量下载街景」工具使用）。"
        )
        # 强制前台执行：Output 参数在后台会被替换成 scratch 临时路径。
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="roads", displayName="路网要素类（线）",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Required")
        p.category = "输入"
        p.filter.list = ["Polyline"]
        params.append(p)

        p = arcpy.Parameter(name="area", displayName="研究区要素类（面，可选）",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Optional")
        p.category = "输入"
        p.filter.list = ["Polygon"]
        params.append(p)

        p = arcpy.Parameter(name="spacing", displayName="采样间距（米）",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 40
        p.category = "采样"
        params.append(p)

        p = arcpy.Parameter(name="offset", displayName="端点偏移（米）",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 15
        p.category = "采样"
        params.append(p)

        p = arcpy.Parameter(name="output_samples", displayName="输出采样点要素类（.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Required")
        p.value = ""
        p.category = "输出"
        params.append(p)

        return params

    def updateMessages(self, parameters):
        out = parameters[4].valueAsText
        if out and out.strip() and not out.lower().endswith((".shp", ".gpkg")):
            parameters[4].setErrorMessage("输出仅支持 .shp / .gpkg 扩展名")
        return

    def execute(self, parameters, messages):
        _inject_paths()

        from streetview_core.sampling import sample_from_fc
        from streetview_core.writer import write_samples
        from geo_common.params import resolve_feature_class

        # roads/area 兼容「从地图拖入图层」：图层对象→dataSource 路径，字符串原样
        roads = resolve_feature_class(parameters[0].value)
        area = resolve_feature_class(parameters[1].value)
        spacing = int(parameters[2].value or 40)
        offset = int(parameters[3].value or 15)
        output_samples = parameters[4].valueAsText

        if not roads:
            raise ValueError("未提供路网要素类")
        if not output_samples:
            raise ValueError("未指定输出采样点要素类")

        records = sample_from_fc(roads, spacing=spacing, offset=offset,
                                 area_fc=area or None)
        if not records:
            raise ValueError("未生成任何采样点：路网过短或 spacing/offset 过大")

        sr = arcpy.SpatialReference(4326)
        out = write_samples(output_samples, records, spatial_ref=sr)
        arcpy.AddMessage(f"生成采样点 {len(records)} 个 → {out}")

        arcpy.SetParameterAsText(4, output_samples)
        return


class DownloadStreetView:
    """工具 2：按采样点批量下载百度街景图片（逆向接口，无需 AK）。"""

    def __init__(self):
        self.label = "批量下载街景"
        self.description = (
            "按采样点批量下载百度街景图片（逆向接口，无需 AK），支持四方向 / 全景模式，"
            "输出街景 JPG 到指定目录 + 下载报告点要素类（含图片路径，可在 Pro 中点击查看）。"
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="points_csv", displayName="采样点 CSV（可选）",
                            direction="Input", datatype="DEFile", parameterType="Optional")
        p.filter.list = ["csv"]
        p.category = "输入"
        params.append(p)

        p = arcpy.Parameter(name="points_fc", displayName="采样点点要素类（可选）",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Optional")
        p.category = "输入"
        p.filter.list = ["Point"]
        params.append(p)

        p = arcpy.Parameter(name="points_crs", displayName="采样点坐标系（仅 CSV 生效）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.filter.list = ["wgs84", "gcj02", "bd09"]
        p.value = "wgs84"
        p.category = "输入"
        params.append(p)

        p = arcpy.Parameter(name="mode", displayName="下载模式",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.filter.list = ["4_views", "left_right", "front_back", "panorama"]
        p.value = "4_views"
        p.category = "下载"
        params.append(p)

        p = arcpy.Parameter(name="level", displayName="清晰度（1~4）",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 3
        p.category = "下载"
        params.append(p)

        p = arcpy.Parameter(name="crop_bottom", displayName="裁剪底部比例",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 0.4
        p.category = "下载"
        params.append(p)

        p = arcpy.Parameter(name="crop_top", displayName="裁剪顶部比例",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 0.0
        p.category = "下载"
        params.append(p)

        p = arcpy.Parameter(name="search_radius", displayName="就近搜索半径（米）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 5.0
        p.category = "下载"
        params.append(p)

        p = arcpy.Parameter(name="interval", displayName="请求间隔（秒）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 0.3
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="timeout", displayName="请求超时（秒）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 30.0
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="retries", displayName="单请求最大重试",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 2
        p.category = "请求"
        params.append(p)

        p = arcpy.Parameter(name="images_dir", displayName="输出图片目录",
                            direction="Input", datatype="DEFolder", parameterType="Required")
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="output_report", displayName="下载报告要素类（.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Required")
        p.value = ""
        p.category = "输出"
        params.append(p)

        return params

    def updateParameters(self, parameters):
        # 采样点坐标系仅在 CSV 输入时生效
        has_csv = bool(parameters[0].valueAsText and parameters[0].valueAsText.strip())
        parameters[2].enabled = has_csv
        return

    def updateMessages(self, parameters):
        has_csv = bool(parameters[0].valueAsText and parameters[0].valueAsText.strip())
        has_fc = bool(parameters[1].valueAsText and parameters[1].valueAsText.strip())
        if not has_csv and not has_fc:
            parameters[0].setWarningMessage("请提供采样点 CSV 或采样点点要素类（二选一）")
        out = parameters[12].valueAsText
        if out and out.strip() and not out.lower().endswith((".shp", ".gpkg")):
            parameters[12].setErrorMessage("输出仅支持 .shp / .gpkg 扩展名")
        return

    def execute(self, parameters, messages):
        _inject_paths()

        from streetview_core.download import (download_streetview,
                                               load_points_from_csv, load_points_from_fc)
        from streetview_core.writer import write_report
        from geo_common.params import resolve_feature_class

        points_csv = parameters[0].valueAsText
        # points_fc 兼容「从地图拖入图层」：图层对象→dataSource 路径，字符串原样
        points_fc = resolve_feature_class(parameters[1].value)
        points_crs = (parameters[2].valueAsText or "wgs84").strip()
        mode = (parameters[3].valueAsText or "4_views").strip()
        level = int(parameters[4].value or 3)
        crop_bottom = float(parameters[5].value or 0.4)
        crop_top = float(parameters[6].value or 0.0)
        search_radius = float(parameters[7].value or 5.0)
        interval = float(parameters[8].value or 0.3)
        timeout = float(parameters[9].value or 30)
        retries = int(parameters[10].value or 2)
        images_dir = parameters[11].valueAsText
        output_report = parameters[12].valueAsText

        # 加载采样点（CSV 优先，其次点要素类）
        if points_csv and points_csv.strip():
            points = load_points_from_csv(points_csv, points_crs)
        elif points_fc and points_fc.strip():
            points = load_points_from_fc(points_fc)
        else:
            raise ValueError("请提供采样点 CSV 或采样点点要素类（二选一）")
        if not points:
            raise ValueError("采样点为空")
        if not images_dir:
            raise ValueError("未指定输出图片目录")
        if not output_report:
            raise ValueError("未指定下载报告要素类")

        arcpy.AddMessage(f"采样点 {len(points)} 个，模式 {mode}，清晰度 {level}")

        def _on_point(done, total, pid, counters):
            arcpy.AddMessage(
                f"  [{done}/{total}] {pid}  成功 {counters['ok']} 跳过 {counters['skip']} "
                f"无街景 {counters['no_pano']} 失败 {counters['fail']}")

        report = download_streetview(
            points, images_dir, mode=mode, level=level,
            crop_bottom=crop_bottom, crop_top=crop_top,
            search_radius=search_radius, interval=interval,
            timeout=timeout, retries=retries, on_point=_on_point)

        sr = arcpy.SpatialReference(4326)
        out = write_report(output_report, report, spatial_ref=sr)

        n_ok = sum(1 for r in report if r["status"] == "ok")
        n_no = sum(1 for r in report if r["status"] == "no_pano")
        n_fail = sum(1 for r in report if r["status"] == "download_failed")
        arcpy.AddMessage(
            f"下载完成：共 {len(report)} 点，成功 {n_ok}，无街景 {n_no}，失败 {n_fail}")
        arcpy.AddMessage(f"街景图片目录：{images_dir}")
        arcpy.AddMessage(f"下载报告：{out}")

        arcpy.SetParameterAsText(12, output_report)
        return
