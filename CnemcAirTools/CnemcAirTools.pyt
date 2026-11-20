# -*- coding: utf-8 -*-
"""CnemcAirTools —— 中国环境监测总站空气质量数据获取工具箱。

改造自 Urbankernel/cnemc-air（中国环境监测总站 air.cnemc.cn:18007，
城市级/点位级实时与历史空气质量数据，WGS-84，无需 AK、无配额限制）。
两个工具：
  1. 获取空气质量数据 —— 城市名/编码 → 抓取 → 输出点要素类(.shp) + CSV/JSON
  2. 城市列表查询 —— 列全国城市编码/名称/经纬度/AQI，帮确认输入

合规：数据为公开环境监测数据，请控制请求频率，勿绕过访问控制。
"""
import arcpy


class Toolbox(object):
    def __init__(self):
        self.label = "空气质量工具箱"
        self.alias = "CnemcAirTools"
        self.tools = [AirQualityFetcher, CityListQuery]


# ------------------------------------------------------------------ 工具 1：获取空气质量数据

class AirQualityFetcher(object):
    def __init__(self):
        self.label = "获取空气质量数据"
        self.description = "从中国环境监测总站按城市抓取城市级/点位级实时与历史空气质量数据，输出点要素类(.shp) + CSV/JSON（WGS-84）"
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="city_names", displayName="城市名（逗号分隔，如 北京市,上海市）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "目标城市"
        params.append(p)

        p = arcpy.Parameter(name="city_codes", displayName="城市编码（逗号分隔，如 110000,310000）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "目标城市"
        params.append(p)

        p = arcpy.Parameter(name="cities_file", displayName="城市清单文件（可选，txt，每行一个城市名/编码）",
                            direction="Input", datatype="DEFile", parameterType="Optional")
        p.filter.list = ["txt"]
        p.category = "目标城市"
        params.append(p)

        p = arcpy.Parameter(name="fetch_city", displayName="抓取城市级（实时/近24小时/近14天日报）",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.category = "抓取内容"
        p.value = True
        params.append(p)

        p = arcpy.Parameter(name="fetch_stations", displayName="抓取点位级实时",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.category = "抓取内容"
        p.value = True
        params.append(p)

        p = arcpy.Parameter(name="fetch_station_history", displayName="抓取点位近24小时（点位数多时耗时较长）",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.category = "抓取内容"
        p.value = False
        params.append(p)

        p = arcpy.Parameter(name="qps", displayName="QPS（每秒请求数上限）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.category = "抓取内容"
        p.value = 1.0
        params.append(p)

        p = arcpy.Parameter(name="out_dir", displayName="输出目录（写 5 个 .shp + CSV/JSON）",
                            direction="Input", datatype="DEFolder", parameterType="Required")
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="csv_on", displayName="输出 CSV",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.category = "输出"
        p.value = True
        params.append(p)

        p = arcpy.Parameter(name="json_on", displayName="输出 JSON",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.category = "输出"
        p.value = True
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
                _roots = [_os.path.join(_env, "CnemcAirTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from cnemc_air_core.pipeline import run_air

        city_names = parameters[0].valueAsText or None
        city_codes = parameters[1].valueAsText or None
        cities_file = parameters[2].valueAsText or None
        fetch_city = bool(parameters[3].value)
        fetch_stations = bool(parameters[4].value)
        fetch_station_history = bool(parameters[5].value)
        qps = float(parameters[6].value or 1.0)
        out_dir = parameters[7].valueAsText
        csv_on = bool(parameters[8].value)
        json_on = bool(parameters[9].value)

        try:
            summary = run_air(
                city_names=city_names, city_codes=city_codes, cities_file=cities_file,
                fetch_city=fetch_city, fetch_stations=fetch_stations,
                fetch_station_history=fetch_station_history,
                qps=qps, out_dir=out_dir, csv_on=csv_on, json_on=json_on,
                log=messages.AddMessage,
            )
        except ValueError as e:
            messages.AddError(str(e))
            raise

        messages.AddMessage("===== 导出成果 =====")
        for f in summary["out_files"]:
            messages.AddMessage(f"  {f}")
        return


# ------------------------------------------------------------------ 工具 2：城市列表查询

class CityListQuery(object):
    def __init__(self):
        self.label = "城市列表查询"
        self.description = "拉取全国城市列表（编码/名称/经纬度/AQI），确认城市名或编码，可选导出 CSV"
        self.canRunInBackground = False

    def getParameterInfo(self):
        params = []

        p = arcpy.Parameter(name="city_filter", displayName="城市名筛选（可选，逗号分隔多个，留空则列出全国）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "查询"
        params.append(p)

        p = arcpy.Parameter(name="out_csv", displayName="输出城市列表 CSV（可选，留空则不输出）",
                            direction="Output", datatype="DEFile", parameterType="Optional")
        p.filter.list = ["csv"]
        p.category = "查询"
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
                _roots = [_os.path.join(_env, "CnemcAirTools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from cnemc_air_core.pipeline import list_cities

        city_filter = parameters[0].valueAsText or None
        out_csv = parameters[1].valueAsText or None

        try:
            result = list_cities(city_filter=city_filter, out_csv=out_csv, log=messages.AddMessage)
        except RuntimeError as e:
            messages.AddError(str(e))
            raise

        messages.AddMessage("===== 城市列表 =====")
        for r in result["rows"]:
            messages.AddMessage(
                f"  {r['city_code']}\t{r['city_name']}\t"
                f"lon={r['lon']}\tlat={r['lat']}\tAQI={r['aqi']}\t{r['quality']}")
        if result["out_csv"]:
            messages.AddMessage(f"已导出: {result['out_csv']}")
            arcpy.SetParameterAsText(1, result["out_csv"])
        return
