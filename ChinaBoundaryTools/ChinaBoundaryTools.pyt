# -*- coding: utf-8 -*-
"""ChinaBoundaryTools —— 中国行政区边界数据获取工具箱。

改造自 Urbankernel/chinaboundary（阿里云 DataV.GeoAtlas 国/省/市/区县四级边界，
WGS-84，无需 AK、无配额限制）。
两个工具：
  1. 获取行政区边界 —— 名称/adcode → 抓取边界 → 输出面要素类(.shp) + 可选 GeoJSON
  2. 名称解析预览 —— 纯本地字典解析（不发请求），确认「名称→adcode」、查歧义

合规：数据来源 DataV.GeoAtlas（高德官方），仅供学习交流；国界权威表述以中国官方为准。
"""
import arcpy


class Toolbox(object):
    def __init__(self):
        self.label = "行政区边界工具箱"
        self.alias = "ChinaBoundaryTools"
        self.tools = [BoundaryFetcher, ResolvePreview]


# ------------------------------------------------------------------ 工具 1：获取行政区边界

class BoundaryFetcher(object):
    def __init__(self):
        self.label = "获取行政区边界"
        self.description = "从 DataV.GeoAtlas 按名称/adcode 获取国/省/市/区县边界，输出面要素类 + 可选 GeoJSON（WGS-84）"
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="level", displayName="行政区层级",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "目标"
        p.filter.list = ["auto", "country", "province", "city", "district"]
        p.value = "auto"
        params.append(p)

        p = arcpy.Parameter(name="name", displayName="行政区名称（逗号分隔，如 广东省,山东省）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "目标"
        params.append(p)

        p = arcpy.Parameter(name="adcode", displayName="行政区编码 adcode（逗号分隔，如 440000,370000）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "目标"
        params.append(p)

        p = arcpy.Parameter(name="depth", displayName="下钻层数（0=本级，1=子级，2=孙级…，上限 3）",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.category = "目标"
        p.value = 0
        params.append(p)

        p = arcpy.Parameter(name="children_only", displayName="仅取子级（不含本级轮廓）",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.category = "目标"
        p.value = False
        params.append(p)

        p = arcpy.Parameter(name="out_fc", displayName="输出边界面要素类（.shp）",
                            direction="Output", datatype="Shapefile", parameterType="Required")
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="out_geojson", displayName="输出 GeoJSON（可选，留空则不输出）",
                            direction="Output", datatype="DEFile", parameterType="Optional")
        p.filter.list = ["json"]
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="cache_dir", displayName="缓存目录（可选，默认关闭）",
                            direction="Input", datatype="DEFolder", parameterType="Optional")
        p.category = "缓存"
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
                _roots = [_os.path.join(_env, "ChinaBoundaryTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from china_boundary_core.pipeline import run_boundary
        from china_boundary_core.resolver import ResolveError, AmbiguousName

        level = parameters[0].valueAsText
        name = parameters[1].valueAsText or None
        adcode = parameters[2].valueAsText or None
        depth = int(parameters[3].value or 0)
        children_only = bool(parameters[4].value)
        out_fc = parameters[5].valueAsText
        out_geojson = parameters[6].valueAsText or None
        cache_dir = parameters[7].valueAsText or None

        # DEFile 不会自动补 .json，与 .shp 行为对齐，缺扩展名时补上
        if out_geojson and not out_geojson.lower().endswith(".json"):
            out_geojson += ".json"

        try:
            result = run_boundary(
                level=level, name=name, adcode=adcode, depth=depth,
                children_only=children_only, out_fc=out_fc,
                out_geojson=out_geojson, cache_dir=cache_dir,
                log=messages.AddMessage,
            )
        except (ResolveError, ValueError) as e:
            messages.AddError(str(e))
            raise

        messages.AddMessage(f"完成：抓取 {result['features']} 个边界要素")
        if result.get("out_fc"):
            messages.AddMessage(f"面要素类：{result['out_fc']}")
            arcpy.SetParameterAsText(5, result["out_fc"])
        if result.get("out_geojson"):
            messages.AddMessage(f"GeoJSON：{result['out_geojson']}")
            arcpy.SetParameterAsText(6, result["out_geojson"])
        return


# ------------------------------------------------------------------ 工具 2：名称解析预览

class ResolvePreview(object):
    def __init__(self):
        self.label = "名称解析预览"
        self.description = "纯本地字典解析（不发请求），确认「名称/adcode→行政区」、查重名歧义"
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="level", displayName="行政区层级",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "目标"
        p.filter.list = ["auto", "country", "province", "city", "district"]
        p.value = "auto"
        params.append(p)

        p = arcpy.Parameter(name="name", displayName="行政区名称（逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "目标"
        params.append(p)

        p = arcpy.Parameter(name="adcode", displayName="行政区编码 adcode（逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "目标"
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
                _roots = [_os.path.join(_env, "ChinaBoundaryTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from china_boundary_core.pipeline import preview_resolve
        from china_boundary_core.resolver import ResolveError, AmbiguousName

        level = parameters[0].valueAsText
        name = parameters[1].valueAsText or None
        adcode = parameters[2].valueAsText or None

        messages.AddMessage("===== 名称/编码解析预览（不发请求）=====")
        try:
            results = preview_resolve(level=level, name=name, adcode=adcode,
                                      log=messages.AddMessage)
        except AmbiguousName as e:
            messages.AddWarning("存在重名歧义：")
            for line in str(e).splitlines():
                messages.AddWarning(line)
            return
        except (ResolveError, ValueError) as e:
            messages.AddError(str(e))
            raise

        for r in results:
            messages.AddMessage(
                f"  {r['input']} → 公开码 {r['public_adcode']} / "
                f"后端码 {r['backend_adcode']} / level={r['level']} / 路径：{r['path']}"
            )
        return
