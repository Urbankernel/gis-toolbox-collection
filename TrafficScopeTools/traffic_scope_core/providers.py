"""路况数据源元信息：百度 / 高德 双源配置。

数据源均为地图网页端路况图层**实际请求**的栅格 PNG（无需 AK、无配额限制）：
- 百度 `https://traffic.map.baidu.com/traffic/TrafficTileService`（map.baidu.com 渲染同源）；
- 高德 `https://tm.amap.com/trafficengine/mapabc/traffictile`（www.amap.com 渲染同源）。

合规：两接口均为地图厂商未公开文档接口，仅限自用研究，禁止批量抓取与再分发。
"""

from __future__ import annotations

# 状态码 → (RGB, 中文名)。中文名两源一致，RGB 不同。
STATUS = {
    0: ("", "无数据"),
    1: ((79, 210, 125), "畅通"),
    2: ((255, 208, 69), "缓行"),
    3: ((232, 14, 14), "拥堵"),
    4: ((181, 0, 0), "严重拥堵"),
}

PROVIDERS = {
    "baidu": {
        "label": "百度",
        "url": "https://traffic.map.baidu.com/traffic/TrafficTileService",
        "referer": "https://map.baidu.com/",
        "min_level": 12,
        "max_level": 18,
        "palette": {1: (79, 210, 125), 2: (255, 208, 69),
                    3: (232, 14, 14), 4: (181, 0, 0)},
    },
    "amap": {
        "label": "高德",
        "url": "https://tm.amap.com/trafficengine/mapabc/traffictile",
        "referer": "https://www.amap.com/",
        "min_level": 10,
        "max_level": 18,
        "palette": {1: (51, 177, 0), 2: (255, 204, 0),
                    3: (222, 0, 0), 4: (150, 20, 20)},
    },
}

PROVIDER_LEVEL_RANGE = {"baidu": (12, 18), "amap": (10, 18)}
AUTO_LEVEL = "auto"

# 最近邻分类参数（与网页端参考实现一致）：每像素取与四级参考色平方欧氏距离
# 最近者，距离超过阈值判「无数据」（排除道路描边近白与透明底抗锯齿过渡像素）。
DIST_THRESHOLD = 3000
# 半透明像素 alpha 低于此值不参与分类（排除抗锯齿边缘噪声）
MIN_ALPHA = 128

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def headers(provider: str) -> dict[str, str]:
    return {"User-Agent": _UA, "Referer": PROVIDERS[provider]["referer"]}


def status_name(code: int) -> str:
    return STATUS[code][1]


def auto_level(area_km2: float, provider: str) -> int:
    """按范围面积自适应推荐瓦片级别。

    实测：≤25 km²→17、25–200 km²→16、>200 km²→15（百度）；高德同级别
    分辨率约为百度 2 倍，故同面积高德比百度低 1 级。
    """
    if area_km2 <= 25:
        level = 17
    elif area_km2 <= 200:
        level = 16
    else:
        level = 15
    if provider == "amap":
        level = max(level - 1, PROVIDER_LEVEL_RANGE["amap"][0])
    return level
