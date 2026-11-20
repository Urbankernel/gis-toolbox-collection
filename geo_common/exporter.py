"""arcpy 直写 SHP / GPKG 点要素类（流式增量落盘）。

这是「薄 .pyt + 核心包」架构里唯一的 arcpy 依赖模块，也是本次改造替换原项目
store.py / export.py（CSV / GeoJSON / geopandas 那一层）的核心。

职责：
- 按输出路径扩展名分流 .shp / .gpkg；
- 用 arcpy.CreateFeatureclass 建点要素类，AddField 建字段；
- 用 arcpy.da.InsertCursor 逐批写入（要素类本身就是增量存储，天然支持断点续跑）；
- SHP 字段名 ≤10 字符映射 / 截断、文本字段 ≤254 字符截断；GPKG 保留原名、文本可更长；
- existing_ids() 读回已写入要素的 id 列，供断点续跑重建去重集合。

坐标系纪律：内部与成果统一 WGS-84（EPSG:4326）。需要投影到其他坐标系时，
由调用方在写入完成后用 arcpy.management.Project 处理（不在本模块内做，保持纯粹）。

注意：arcpy 在函数内延迟导入，非 arcpy 环境 import 本模块不报错，
便于其余纯逻辑模块在命令行 pytest 验证。
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Sequence

# SHP 字段名上限（DBF 限制）
_SHP_NAME_MAX = 10
# SHP 文本字段长度上限（DBF 限制）
_SHP_TEXT_LEN = 254
# GPKG 文本字段长度（可变长，此处给一个足够大的上限）
_GPKG_TEXT_LEN = 4000


def _fmt(out_path: str) -> str:
    """根据扩展名判断输出格式：shp / gpkg。"""
    low = out_path.lower()
    if low.endswith(".shp"):
        return "shp"
    if low.endswith(".gpkg"):
        return "gpkg"
    raise ValueError(f"输出仅支持 .shp / .gpkg，当前: {out_path!r}")


def _shp_field_name(name: str, rename_map: dict[str, str] | None) -> str:
    """SHP 字段名 ≤10 字符：优先用映射，否则截断。"""
    if rename_map and name in rename_map:
        return rename_map[name]
    if len(name) > _SHP_NAME_MAX:
        return name[:_SHP_NAME_MAX]
    return name


class PointExporter:
    """把点要素流式写入 SHP / GPKG（WGS-84）。

    参数:
        out_path: 输出路径（.shp 或 .gpkg）
        field_defs: 字段定义 [(name, type), ...]，type ∈ TEXT/DOUBLE/LONG/SHORT
        rename_map: SHP 超长字段名映射 {原名: 短名}（可选）
        spatial_ref: arcpy.SpatialReference，默认 WGS-84 (4326)
    """

    def __init__(
        self,
        out_path: str,
        field_defs: Sequence[tuple[str, str]],
        rename_map: dict[str, str] | None = None,
        spatial_ref: Any = None,
    ):
        self.out_path = out_path
        self.fmt = _fmt(out_path)
        self.rename_map = rename_map or {}
        self.text_len = _SHP_TEXT_LEN if self.fmt == "shp" else _GPKG_TEXT_LEN

        # 原始字段名（保持 field_defs 顺序，与写入顺序一致）
        self.orig_names: list[str] = [name for name, _ in field_defs]
        self.field_types: dict[str, str] = {name: typ.upper() for name, typ in field_defs}
        # 实际写入的字段名（SHP 映射 / 截断后）
        self.write_names: list[str] = [
            _shp_field_name(n, self.rename_map) if self.fmt == "shp" else n
            for n in self.orig_names
        ]

        self._spatial_ref = spatial_ref
        self._fc: str | None = None   # 要素类完整路径
        self._cursor = None

    # ------------------------------------------------------------------ 路径解析

    def _resolve(self) -> tuple[str, str]:
        """返回 (workspace, name)：SHP 为 (目录, 文件名)，GPKG 为 (gpkg 文件, 图层名)。"""
        if self.fmt == "shp":
            ws = os.path.dirname(os.path.abspath(self.out_path))
            name = os.path.splitext(os.path.basename(self.out_path))[0]
            return ws, name
        # gpkg：workspace 即 gpkg 文件本身
        return self.out_path, "pois"

    def _fc_path(self, ws: str, name: str) -> str:
        """要素类完整路径：SHP 为 dir/name.shp，GPKG 为 gpkg文件/图层名。"""
        if self.fmt == "shp":
            return os.path.join(ws, name + ".shp")
        return os.path.join(ws, name)

    # ------------------------------------------------------------------ 创建 / 打开

    def _import_arcpy(self):
        import arcpy  # 延迟导入
        return arcpy

    def _spatial_ref_obj(self, arcpy):
        if self._spatial_ref is not None:
            return self._spatial_ref
        return arcpy.SpatialReference(4326)

    def _delete_existing(self, arcpy) -> None:
        """删除既有输出（新建模式覆盖）。"""
        if self.fmt == "shp":
            base = os.path.splitext(self.out_path)[0]
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx"):
                p = base + ext
                if os.path.exists(p):
                    arcpy.management.Delete(p)
        elif os.path.exists(self.out_path):
            arcpy.management.Delete(self.out_path)

    def _ensure_gpkg(self, arcpy) -> None:
        """GPKG 文件不存在时先创建 GeoPackage 数据库。"""
        if not os.path.exists(self.out_path):
            arcpy.management.CreateSQLiteDatabase(self.out_path, "GEOPACKAGE")

    def _add_fields(self, arcpy) -> None:
        for orig, write in zip(self.orig_names, self.write_names):
            typ = self.field_types[orig]
            if typ == "TEXT":
                arcpy.management.AddField(self._fc, write, "TEXT",
                                          field_length=self.text_len)
            else:
                arcpy.management.AddField(self._fc, write, typ)

    def create(self, overwrite: bool = True) -> "PointExporter":
        """新建要素类并打开写入游标。"""
        arcpy = self._import_arcpy()
        ws, name = self._resolve()

        if overwrite:
            self._delete_existing(arcpy)
        if self.fmt == "gpkg":
            self._ensure_gpkg(arcpy)

        sr = self._spatial_ref_obj(arcpy)
        arcpy.management.CreateFeatureclass(ws, name, "POINT", spatial_reference=sr)
        self._fc = self._fc_path(ws, name)
        self._add_fields(arcpy)

        cursor_fields = list(self.write_names) + ["SHAPE@XY"]
        self._cursor = arcpy.da.InsertCursor(self._fc, cursor_fields)
        return self

    def attach(self) -> "PointExporter":
        """打开已有要素类追加（断点续跑）。"""
        arcpy = self._import_arcpy()
        ws, name = self._resolve()
        self._fc = self._fc_path(ws, name)
        cursor_fields = list(self.write_names) + ["SHAPE@XY"]
        self._cursor = arcpy.da.InsertCursor(self._fc, cursor_fields)
        return self

    # ------------------------------------------------------------------ 写入

    def write_batch(self, rows: Iterable[dict[str, Any]]) -> int:
        """批量写入。每行 dict 需含全部 orig_names 字段 + 'lon'/'lat' 键。"""
        if self._cursor is None:
            raise RuntimeError("尚未 create() 或 attach()，无法写入")
        n = 0
        for row in rows:
            values = []
            for orig in self.orig_names:
                typ = self.field_types[orig]
                v = row.get(orig)
                if typ == "TEXT":
                    v = "" if v is None else str(v)
                    v = v[:self.text_len]
                values.append(v)
            values.append((float(row["lon"]), float(row["lat"])))
            self._cursor.insertRow(values)
            n += 1
        return n

    # ------------------------------------------------------------------ 续跑辅助

    def existing_ids(self, id_field: str = "poi_id") -> set[str]:
        """读回已写入要素的 id 列（断点续跑重建去重集合）。"""
        arcpy = self._import_arcpy()
        if self._fc is None:
            self._resolve_fc()
        if not arcpy.Exists(self._fc):
            return set()
        ids: set[str] = set()
        with arcpy.da.SearchCursor(self._fc, [id_field]) as cur:
            for (v,) in cur:
                if v is not None:
                    ids.add(str(v))
        return ids

    def _resolve_fc(self) -> None:
        ws, name = self._resolve()
        self._fc = self._fc_path(ws, name)

    def close(self) -> None:
        if self._cursor is not None:
            del self._cursor
            self._cursor = None

    def __enter__(self) -> "PointExporter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ------------------------------------------------------------------ 面图层导出

# grid 网格面字段（grid_id 溯源、depth 四分裂深度、count 该网格 POI 总数）
GRID_FIELD_DEFS: list[tuple[str, str]] = [
    ("grid_id", "TEXT"),
    ("depth", "LONG"),
    ("count", "LONG"),
]

# area 范围面字段
AREA_FIELD_DEFS: list[tuple[str, str]] = [
    ("name", "TEXT"),
]


def write_polygons(
    out_path: str,
    features: Sequence[dict[str, Any]],
    field_defs: Sequence[tuple[str, str]],
    *,
    spatial_ref: Any = None,
    layer_name: str = "grid",
    rename_map: dict[str, str] | None = None,
) -> str:
    """一次性写面要素类（SHP / GPKG），用于 grid 网格面 / area 范围面。"""
    return _write_features(out_path, features, field_defs, "POLYGON",
                           spatial_ref=spatial_ref, layer_name=layer_name,
                           rename_map=rename_map)


def write_lines(
    out_path: str,
    features: Sequence[dict[str, Any]],
    field_defs: Sequence[tuple[str, str]],
    *,
    spatial_ref: Any = None,
    layer_name: str = "line",
    rename_map: dict[str, str] | None = None,
) -> str:
    """一次性写线要素类（SHP / GPKG），用于公交线路轨迹等 LineString。"""
    return _write_features(out_path, features, field_defs, "POLYLINE",
                           spatial_ref=spatial_ref, layer_name=layer_name,
                           rename_map=rename_map)


def write_points(
    out_path: str,
    features: Sequence[dict[str, Any]],
    field_defs: Sequence[tuple[str, str]],
    *,
    spatial_ref: Any = None,
    layer_name: str = "point",
    rename_map: dict[str, str] | None = None,
) -> str:
    """一次性写点要素类（SHP / GPKG）。"""
    return _write_features(out_path, features, field_defs, "POINT",
                           spatial_ref=spatial_ref, layer_name=layer_name,
                           rename_map=rename_map)


def _write_features(
    out_path: str,
    features: Sequence[dict[str, Any]],
    field_defs: Sequence[tuple[str, str]],
    geometry_type: str,
    *,
    spatial_ref: Any = None,
    layer_name: str = "layer",
    rename_map: dict[str, str] | None = None,
) -> str:
    """通用写要素类（POLYGON / POLYLINE / POINT），SHP / GPKG。

    参数:
        out_path: 完整输出路径（.shp / .gpkg）
        features: 要素列表，每项含 "geom"（WGS-84 shapely 几何）+ 各字段值
        field_defs: [(name, type), ...]，type ∈ TEXT/LONG/DOUBLE/SHORT
        geometry_type: "POLYGON" / "POLYLINE" / "POINT"
        spatial_ref: 目标坐标系 arcpy.SpatialReference；非 WGS-84 时内部 projectAs 投影
        layer_name: GPKG 图层名（SHP 用文件名，忽略此参数）
        rename_map: SHP 超长字段名映射 {原名: 短名}（可选，未提供则截断）

    返回:
        写入的要素类路径。
    """
    import arcpy  # 延迟导入

    fmt = _fmt(out_path)
    wgs84 = arcpy.SpatialReference(4326)
    target_sr = spatial_ref if spatial_ref is not None else wgs84
    need_project = target_sr.factoryCode != 4326

    # 解析 workspace / 名称
    if fmt == "shp":
        ws = os.path.dirname(os.path.abspath(out_path))
        name = os.path.splitext(os.path.basename(out_path))[0]
        fc = os.path.join(ws, name + ".shp")
    else:
        ws = out_path
        name = layer_name
        fc = os.path.join(out_path, layer_name)
        if not os.path.exists(out_path):
            arcpy.management.CreateSQLiteDatabase(out_path, "GEOPACKAGE")

    # 删除既有同名要素类
    if arcpy.Exists(fc):
        arcpy.management.Delete(fc)

    arcpy.management.CreateFeatureclass(ws, name, geometry_type, spatial_reference=target_sr)

    # 字段（SHP 优先按 rename_map 映射，否则截断到 ≤10 字符）
    orig_names = [n for n, _ in field_defs]
    write_names = [_shp_field_name(n, rename_map) if fmt == "shp" else n for n in orig_names]
    type_map = dict(field_defs)
    for orig, write in zip(orig_names, write_names):
        typ = type_map[orig].upper()
        if typ == "TEXT":
            arcpy.management.AddField(fc, write, "TEXT", field_length=254)
        else:
            arcpy.management.AddField(fc, write, typ)

    # 写入（shapely WGS-84 → arcpy，必要时 projectAs 到目标坐标系）
    cursor_fields = list(write_names) + ["SHAPE@"]
    with arcpy.da.InsertCursor(fc, cursor_fields) as cur:
        for feat in features:
            values = []
            for orig, typ in field_defs:
                v = feat.get(orig)
                if typ.upper() == "TEXT":
                    v = "" if v is None else str(v)
                elif v is None:
                    # 数值字段（DOUBLE/LONG/SHORT）不允许 NULL（SHP 尤甚），None 统一按 0
                    v = 0.0 if typ.upper() == "DOUBLE" else 0
                values.append(v)
            geom = feat["geom"]
            shape = arcpy.FromWKB(bytes(geom.wkb), wgs84)
            if need_project:
                shape = shape.projectAs(target_sr)
            values.append(shape)
            cur.insertRow(values)
    return fc
