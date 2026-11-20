"""streetview_core —— 百度街景下载核心包。

三层架构里的「核心包」：纯逻辑（bearing / pano_slice / pano_client 无 arcpy 依赖，
可命令行测试）+ arcpy 桥接层（sampling / writer / download 里延迟 import arcpy）。

数据源：百度全景逆向接口（qt=qsdata/pdata/sdata，无需 AK），坐标链
WGS84 → BD09MC 仅用于发请求（复用 geo_common.coords），成果一律 WGS84。
"""
