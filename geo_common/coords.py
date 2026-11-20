"""中国常用坐标系互转：GCJ-02（火星坐标）/ BD-09（百度坐标）/ WGS-84。

覆盖高德（GCJ-02）与百度（BD-09）两大国内数据源。

- 高德 Web 服务 API 的输入/输出均为 GCJ-02。
- 百度地图相关接口使用 BD-09。
- 系统内部与 GIS 成果文件统一 WGS-84。

公式来源：GCJ-02 经验公式 + 对称反向逼近（原 amap-poi-fetcher coords.py 移植），
BD-09 偏移为百度公开的经典换算公式。数值行为与原项目一致。
"""

from __future__ import annotations

import math

PI = 3.1415926535897932384626  # π
A = 6378245.0  # 长半轴
EE = 0.00669342162296594323  # 偏心率平方
X_PI = PI * 3000.0 / 180.0  # BD-09 偏移用的 π 常量


def _out_of_china(lng: float, lat: float) -> bool:
    """是否在中国境外（境外坐标无火星偏移，原样返回）。"""
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(lng: float, lat: float) -> float:
    """纬度偏移辅助计算（多项式 + 三角函数经验公式）。"""
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + \
          0.1 * lng * lat + 0.2 * math.sqrt(math.fabs(lng))
    ret += (20.0 * math.sin(6.0 * lng * PI) + 20.0 *
            math.sin(2.0 * lng * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * PI) + 40.0 *
            math.sin(lat / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * PI) + 320 *
            math.sin(lat * PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    """经度偏移辅助计算（多项式 + 三角函数经验公式）。"""
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + \
          0.1 * lng * lat + 0.1 * math.sqrt(math.fabs(lng))
    ret += (20.0 * math.sin(6.0 * lng * PI) + 20.0 *
            math.sin(2.0 * lng * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * PI) + 40.0 *
            math.sin(lng / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * PI) + 300.0 *
            math.sin(lng / 30.0 * PI)) * 2.0 / 3.0
    return ret


# ------------------------------------------------------------------ GCJ-02 ↔ WGS-84

def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02 → WGS-84。境外坐标原样返回。"""
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
    dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
    mglat = lat + dlat
    mglng = lng + dlng
    # 利用对称原理反向逼近 WGS-84
    return lng * 2 - mglng, lat * 2 - mglat


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """WGS-84 → GCJ-02（正向偏移，用于向高德发请求的坐标）。境外坐标原样返回。

    与 gcj02_to_wgs84 近似互逆（往返误差 < 1e-4°，即米级）。
    """
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
    dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
    return lng + dlng, lat + dlat


# ------------------------------------------------------------------ GCJ-02 ↔ BD-09

def gcj02_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02 → BD-09（百度再加密）。"""
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * X_PI)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * X_PI)
    bd_lng = z * math.cos(theta) + 0.0065
    bd_lat = z * math.sin(theta) + 0.006
    return bd_lng, bd_lat


def bd09_to_gcj02(bd_lng: float, bd_lat: float) -> tuple[float, float]:
    """BD-09 → GCJ-02（百度坐标去偏）。"""
    x = bd_lng - 0.0065
    y = bd_lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    gcj_lng = z * math.cos(theta)
    gcj_lat = z * math.sin(theta)
    return gcj_lng, gcj_lat


# ------------------------------------------------------------------ BD-09 ↔ WGS-84（组合）

def bd09_to_wgs84(bd_lng: float, bd_lat: float) -> tuple[float, float]:
    """BD-09 → WGS-84（经 GCJ-02 中转）。"""
    return gcj02_to_wgs84(*bd09_to_gcj02(bd_lng, bd_lat))


def wgs84_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    """WGS-84 → BD-09（经 GCJ-02 中转）。"""
    return gcj02_to_bd09(*wgs84_to_gcj02(lng, lat))


# ------------------------------------------------------------------ bd09mc（百度墨卡托）↔ BD-09

# 百度墨卡托分带参数（经度偏移），正向/反向各一套多项式系数。
MCBAND = [12890594.86, 8362377.87, 5591021.0, 3481989.83, 1678043.12, 0.0]
MC2LL = [
    [1.410526172116255e-8, 0.00000898305509648872, -1.9939833816331,
     200.9824383106796, -187.2403703815547, 91.6087516669843,
     -23.38765649603339, 2.57121317296198, -0.03801003308653, 17337981.2],
    [-7.435856389565537e-9, 0.000008983055097726239, -0.78625201886289,
     96.32687599759846, -1.85204757529826, -59.36935905485877,
     47.40033549296737, -16.50741931063887, 2.28786674699375, 10260144.86],
    [-3.030883460898826e-8, 0.00000898305509983578, 0.30071316287616,
     59.74293618442277, 7.357984074871, -25.38371002664745,
     13.45380521110908, -3.29883767235584, 0.32710905363475, 6856817.37],
    [-1.981981304930552e-8, 0.000008983055099779535, 0.03278182852591,
     40.31678527705744, 0.65659298677277, -4.44255534477492,
     0.85341911805263, 0.12923347998204, -0.04625736007561, 4482777.06],
    [3.09191371068437e-9, 0.000008983055096812155, 0.00006995724062,
     23.10934304144901, -0.00023663490511, -0.6321817810242,
     -0.00663494467273, 0.03430082397953, -0.00466043876332, 2555164.4],
    [2.890871144776878e-9, 0.000008983055095805407, -3.068298e-8,
     7.47137025468032, -0.00000353937994, -0.02145144861037,
     -0.00001234426596, 0.00010322952773, -0.00000323890364, 826088.5],
]
LLBAND = [75, 60, 45, 30, 15, 0]
LL2MC = [
    [-0.0015702102444, 111320.7020616939, 1704480524535203, -10338987376042340,
     26112667856603880, -35149669176653700, 26595700718403920,
     -10725012454188240, 1800819912950474, 82.5],
    [0.0008277824516172526, 111320.7020463578, 647795574.6671607,
     -4082003173.641316, 10774905663.51142, -15171875531.51559,
     12053065338.62167, -5124939663.577472, 913311935.9512032, 67.5],
    [0.00337398766765, 111320.7020202162, 4481351.045890365,
     -23393751.19931662, 79682215.47186455, -115964993.2797253,
     97236711.15602145, -43661946.33752821, 8477230.501135234, 52.5],
    [0.00220636496208, 111320.7020209128, 51751.86112841131, 3796837.749470245,
     992013.7397791013, -1221952.21711287, 1340652.697009075,
     -620943.6990984312, 144416.9293806241, 37.5],
    [-0.0003441963504368392, 111320.7020576856, 278.2353980772752,
     2485758.690035394, 6070.750963243378, 54821.18345352118,
     9540.606633304236, -2710.55326746645, 1405.483844121726, 22.5],
    [-0.0003218135878613132, 111320.7020701615, 0.00369383431289,
     823725.6402795718, 0.46104986909093, 2351.343141331292,
     1.58060784298199, 8.77738589078284, 0.37238884252424, 7.45],
]


def bd09mc_to_bd09(x: float, y: float) -> tuple[float, float]:
    """bd09mc（百度墨卡托，米制 x,y）→ BD-09 经纬度。"""
    cF = MC2LL[-1]
    for i, band in enumerate(MCBAND):
        if abs(y) >= band:
            cF = MC2LL[i]
            break
    xa, ya = abs(x), abs(y)
    lng = cF[0] + cF[1] * xa
    c = ya / cF[9]
    lat = (cF[2] + cF[3] * c + cF[4] * c ** 2 + cF[5] * c ** 3
           + cF[6] * c ** 4 + cF[7] * c ** 5 + cF[8] * c ** 6)
    return lng * (1 if x >= 0 else -1), lat * (1 if y >= 0 else -1)


def bd09_to_bd09mc(lng: float, lat: float) -> tuple[float, float]:
    """BD-09 经纬度 → bd09mc（百度墨卡托，米制 x,y）。"""
    cE = LL2MC[-1]
    for i, band in enumerate(LLBAND):
        if lat >= band:
            cE = LL2MC[i]
            break
    x = cE[0] + cE[1] * abs(lng)
    tmp = abs(lat) / cE[9]
    y = (cE[2] + cE[3] * tmp + cE[4] * tmp ** 2 + cE[5] * tmp ** 3
         + cE[6] * tmp ** 4 + cE[7] * tmp ** 5 + cE[8] * tmp ** 6)
    return x * (1 if lng > 0 else -1), y * (1 if lat > 0 else -1)


def bd09mc_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """bd09mc（百度墨卡托）→ WGS-84（mc → bd09 → gcj02 → wgs84）。"""
    bd_lng, bd_lat = bd09mc_to_bd09(x, y)
    return bd09_to_wgs84(bd_lng, bd_lat)


def wgs84_to_bd09mc(lng: float, lat: float) -> tuple[float, float]:
    """WGS-84 → bd09mc（百度墨卡托），与 bd09mc_to_wgs84 近似互逆。"""
    return bd09_to_bd09mc(*wgs84_to_bd09(lng, lat))
