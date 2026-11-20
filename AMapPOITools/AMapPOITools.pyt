# -*- coding: utf-8 -*-
"""AMapPOITools —— 高德 POI 批量抓取工具箱（ArcGIS Pro Python Toolbox）。

薄入口：只做「参数定义 + 编排」，核心逻辑下沉到 amap_poi_core 与 geo_common。

依赖导入说明：
- 本 .pyt 所在目录（AMapPOITools/）与父目录（含 geo_common/）都会被加入 sys.path，
  保证 `import amap_poi_core` 与 `import geo_common` 可用；
- geo_common 内的 arcpy 为延迟导入，纯逻辑模块可在命令行 pytest 验证。

输出格式：输出要素类扩展名决定 .shp（字段名 ≤10 字符自动映射、文本 ≤254 截断）
或 .gpkg（保留 35 个原名）。坐标系统一 WGS-84，可用「输出坐标系」参数投影。
"""

import os
import sys

import arcpy

# 让本 .pyt 能 import 同目录的 amap_poi_core，以及父目录的 geo_common。
# 注意：这里只是「前台验证 / 命令行」兜底。ArcGIS Pro「后台执行」时 execute() 跑在
# 独立子进程里（traceback 显示 File "<string>"），此处的 sys.path 注入不会被带到子进程，
# 所以真正的保险写在 execute() 方法体内部（见下），那里用固定绝对路径兜底。
_THIS_DIR = None
try:
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    _PARENT_DIR = os.path.dirname(_THIS_DIR)
    for _d in (_THIS_DIR, _PARENT_DIR):
        if _d and _d not in sys.path:
            sys.path.insert(0, _d)
except NameError:
    # 后台 exec 场景 __file__ 可能未定义，交给 execute() 内部兜底
    pass


class Toolbox:
    def __init__(self):
        self.label = "高德 POI 工具箱"
        self.alias = "AMapPOITools"
        self.tools = [AmapPoiFetcher]


class AmapPoiFetcher:
    """按空间范围批量抓取高德 POI，输出 SHP / GPKG 点要素类。"""

    def __init__(self):
        self.label = "高德 POI 抓取"
        self.description = (
            "给定空间范围（面要素 / 行政区 / 缓冲区 / 矩形）与 POI 类型编码，"
            "递归四分网格绕过接口条数上限，多 key 轮换，抓全范围内 POI，"
            "输出 SHP / GPKG 点要素类（WGS-84）。"
        )
        # 强制前台执行：Output 参数在后台执行时会被 ArcGIS 替换成 scratch 临时路径，
        # 导致结果不落到用户指定路径。前台执行可保证路径原样传入 + AddMessage 进度实时可见。
        self.canRunInBackground = False

    # ------------------------------------------------------------------ 参数

    def getParameterInfo(self):
        params = []

        # ---- 范围 ----
        p = arcpy.Parameter(name="scope_type", displayName="范围类型",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.filter.list = ["面要素", "行政区", "缓冲区", "矩形范围"]
        p.value = "面要素"
        p.category = "范围"
        params.append(p)

        p = arcpy.Parameter(name="in_features", displayName="范围面要素",
                            direction="Input", datatype="GPFeatureLayer", parameterType="Optional")
        p.category = "范围"
        p.filter.list = ["Polygon"]
        params.append(p)

        p = arcpy.Parameter(name="admin_keyword", displayName="行政区（adcode/名称）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "范围"
        params.append(p)

        p = arcpy.Parameter(name="center_point", displayName="中心点（经度,纬度）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "范围"
        params.append(p)

        p = arcpy.Parameter(name="center_coord_type", displayName="输入坐标类型",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.filter.list = ["wgs84", "gcj02"]
        p.value = "wgs84"
        p.category = "范围"
        params.append(p)

        p = arcpy.Parameter(name="radius_km", displayName="缓冲半径（公里）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.category = "范围"
        params.append(p)

        p = arcpy.Parameter(name="bbox", displayName="矩形范围（xmin,ymin,xmax,ymax）",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.category = "范围"
        params.append(p)

        # ---- 凭证 ----
        p = arcpy.Parameter(name="api_keys", displayName="高德 Web 服务 Key（可多选）",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.multiValue = True
        p.category = "凭证"
        params.append(p)

        # ---- 检索 ----
        p = arcpy.Parameter(name="poi_types", displayName="POI 类型编码（逗号分隔）",
                            direction="Input", datatype="GPString", parameterType="Required")
        p.category = "检索"
        params.append(p)

        p = arcpy.Parameter(name="extensions", displayName="返回字段级别",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.filter.list = ["all", "base"]
        p.value = "all"
        p.category = "检索"
        params.append(p)

        # ---- 网格与请求 ----
        p = arcpy.Parameter(name="grid_size", displayName="初始网格边长（度）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 0.1
        p.category = "网格与请求"
        params.append(p)

        p = arcpy.Parameter(name="split_threshold", displayName="四分裂阈值（条）",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 200
        p.category = "网格与请求"
        params.append(p)

        p = arcpy.Parameter(name="max_depth", displayName="最大递归深度",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 4
        p.category = "网格与请求"
        params.append(p)

        p = arcpy.Parameter(name="min_size", displayName="最小网格边长（度）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 0.005
        p.category = "网格与请求"
        params.append(p)

        p = arcpy.Parameter(name="offset", displayName="每页条数（1~25）",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 20
        p.category = "网格与请求"
        params.append(p)

        p = arcpy.Parameter(name="qps", displayName="每秒请求数上限",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 2.0
        p.category = "网格与请求"
        params.append(p)

        p = arcpy.Parameter(name="max_retries", displayName="单请求最大重试",
                            direction="Input", datatype="GPLong", parameterType="Optional")
        p.value = 5
        p.category = "网格与请求"
        params.append(p)

        p = arcpy.Parameter(name="timeout", displayName="请求超时（秒）",
                            direction="Input", datatype="GPDouble", parameterType="Optional")
        p.value = 10.0
        p.category = "网格与请求"
        params.append(p)

        p = arcpy.Parameter(name="filter_by_scope", displayName="按范围过滤界外点",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.value = True
        p.category = "网格与请求"
        params.append(p)

        # ---- 输出 ----
        # 用 Output + DEFeatureClass 提供「输出浏览框」（浏览选择 + 手动输入均可用）。
        # 注意：Output 方向在 ArcGIS Pro「后台执行」时会被替换成 scratch 临时路径，
        # 因此本工具强制前台执行（见 __init__ 的 canRunInBackground=False），确保路径原样传入。
        p = arcpy.Parameter(name="output_features", displayName="输出要素类（.shp / .gpkg）",
                            direction="Output", datatype="DEFeatureClass", parameterType="Required")
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="output_crs", displayName="输出坐标系",
                            direction="Input", datatype="GPString", parameterType="Optional")
        p.value = "GCS_WGS_1984"
        p.category = "输出"
        params.append(p)

        p = arcpy.Parameter(name="resume", displayName="断点续跑",
                            direction="Input", datatype="GPBoolean", parameterType="Optional")
        p.value = False
        p.category = "输出"
        params.append(p)

        return params

    # ------------------------------------------------------------------ 联动

    def updateParameters(self, parameters):
        scope = parameters[0].valueAsText
        if scope is not None:
            parameters[1].enabled = (scope == "面要素")
            parameters[2].enabled = (scope == "行政区")
            parameters[3].enabled = (scope == "缓冲区")
            parameters[4].enabled = (scope in ("缓冲区", "矩形范围"))
            parameters[5].enabled = (scope == "缓冲区")
            parameters[6].enabled = (scope == "矩形范围")
        return

    def updateMessages(self, parameters):
        if parameters[8].valueAsText and parameters[8].valueAsText.strip():
            # offset 合法性（高德上限 25）
            if parameters[14].value is not None and not (1 <= int(parameters[14].value) <= 25):
                parameters[14].setErrorMessage("每页条数必须在 1~25 之间（高德上限 25）")
        # 输出路径扩展名校验
        out = parameters[19].valueAsText
        if out and out.strip() and not out.lower().endswith((".shp", ".gpkg")):
            parameters[19].setErrorMessage("输出仅支持 .shp / .gpkg 扩展名")
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
                _roots = [_os.path.join(_env, "AMapPOITools"), _env]
        for _d in _roots:
            if _d and _d not in _sys.path:
                _sys.path.insert(0, _d)

        from geo_common.exporter import (
            PointExporter, write_polygons, GRID_FIELD_DEFS, AREA_FIELD_DEFS)
        from geo_common.keypool import KeyPool
        from geo_common.params import resolve_feature_class
        from geo_common.state import RunState
        from amap_poi_core.client import AmapClient
        from amap_poi_core.parser import FIELD_DEFS, SHP_RENAME_MAP
        from amap_poi_core.scope import load_scope
        from amap_poi_core.scheduler import Scheduler

        # ---- 读参数 ----
        scope_type = parameters[0].valueAsText
        # in_features 兼容「从地图拖入图层」：图层对象→dataSource 路径，字符串原样
        in_features = resolve_feature_class(parameters[1].value)
        admin_keyword = parameters[2].valueAsText
        center_point = parameters[3].valueAsText
        center_coord_type = parameters[4].valueAsText or "wgs84"
        radius_km = parameters[5].value
        bbox = parameters[6].valueAsText

        # 多值参数：valueAsText 为分号分隔字符串（如 'k1;k2'），.value 是 ValueTable、.values 在 3.5.2 不可靠
        api_keys = [k.strip() for k in (parameters[7].valueAsText or "").split(";") if k.strip()]
        poi_types = (parameters[8].valueAsText or "").strip()
        extensions = parameters[9].valueAsText or "all"

        grid_size = float(parameters[10].value or 0.1)
        split_threshold = int(parameters[11].value or 200)
        max_depth = int(parameters[12].value or 4)
        min_size = float(parameters[13].value or 0.005)
        offset = int(parameters[14].value or 20)
        qps = float(parameters[15].value or 2.0)
        max_retries = int(parameters[16].value or 5)
        timeout = float(parameters[17].value or 10.0)
        filter_by_scope = bool(parameters[18].value)

        output_features = parameters[19].valueAsText
        output_crs = (parameters[20].valueAsText or "GCS_WGS_1984").strip()
        resume = bool(parameters[21].value)

        # ---- 校验 ----
        if not api_keys:
            raise ValueError("未提供高德 Web 服务 Key")
        if not poi_types:
            raise ValueError("未提供 POI 类型编码")
        if not output_features:
            raise ValueError("未指定输出要素类")

        # ---- 组装客户端 ----
        pool = KeyPool(api_keys)
        client = AmapClient(
            pool, qps=qps, max_retries=max_retries, timeout=timeout)

        # ---- 解析范围 ----
        geom, scope_name = load_scope(
            scope_type, client=client,
            in_features=in_features, admin_keyword=admin_keyword,
            center_point=center_point, center_coord_type=center_coord_type,
            radius_km=radius_km, bbox=bbox)
        arcpy.AddMessage(f"范围: {scope_name}，bounds={[round(v, 4) for v in geom.bounds]}")

        # ---- 输出路径与投影 ----
        sr = arcpy.SpatialReference(output_crs)
        need_project = (sr.factoryCode != 4326)
        base, ext = os.path.splitext(output_features)

        # poi 工作路径（need_project 时先写 WGS-84 临时，最后用 Project 工具投影到最终）
        work_path = output_features
        if need_project:
            work_path = base + "_wgs84" + ext
            arcpy.AddMessage(f"将先写 WGS-84 临时文件，再投影到 {output_crs}")

        # grid / area 辅助图层最终路径
        grid_final = base + "_grid" + ext
        area_final = base + "_area" + ext

        # ---- 状态 / 续跑 ----
        state_store = RunState(os.path.splitext(work_path)[0] + "_state.json")
        exporter = PointExporter(work_path, FIELD_DEFS, SHP_RENAME_MAP)

        state = state_store.load()
        can_resume = resume and state is not None and arcpy.Exists(work_path)
        if can_resume:
            exporter.attach()
            arcpy.AddMessage("断点续跑：沿用既有要素类继续追加")
        else:
            exporter.create(overwrite=True)

        # ---- 调度抓取 ----
        scheduler = Scheduler(
            client, exporter,
            types=poi_types, offset=offset, extensions=extensions,
            split_threshold=split_threshold, max_depth=max_depth,
            min_size=min_size, filter_by_scope=filter_by_scope)

        try:
            stats = scheduler.run(
                geom, grid_size, state_store=state_store, resume=can_resume)
        finally:
            exporter.close()

        # ---- 写 grid / area 辅助图层 ----
        write_polygons(
            grid_final, scheduler.grids, GRID_FIELD_DEFS,
            spatial_ref=sr if need_project else None, layer_name="grid")
        write_polygons(
            area_final, [{"name": scope_name, "geom": geom}], AREA_FIELD_DEFS,
            spatial_ref=sr if need_project else None, layer_name="area")
        arcpy.AddMessage(f"网格面: {grid_final}；范围面: {area_final}")

        # ---- 投影 poi（可选） ----
        if need_project:
            arcpy.management.Project(work_path, output_features, sr)
            arcpy.AddMessage(f"已投影到 {output_crs}: {output_features}")

        # ---- 汇报 ----
        arcpy.AddMessage(
            f"完成：入库 {stats.kept} 条（拉取 {stats.fetched}，范围外过滤 {stats.filtered}）；"
            f"处理网格 {stats.grids_done}，四分裂 {stats.grids_split}，"
            f"失败 {stats.grids_failed}，触底截断 {stats.grids_truncated}")

        # 把最终路径回设给 Output 参数，ArcGIS Pro 据此把结果加入地图/内容窗格
        arcpy.SetParameterAsText(19, output_features)
        return
