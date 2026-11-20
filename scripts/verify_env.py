# -*- coding: utf-8 -*-
"""环境自检：探测 8 个工具箱所需的依赖是否就绪，并校验 .pyt 语法可加载。

用法（在 ArcGIS Pro 的 Python 环境中）：
    python scripts/verify_env.py
"""

import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TOOLS = [
    "AMapPOITools", "GetAOITools", "BuslineTools", "StreetViewTools",
    "IsochroneTools", "TrafficScopeTools", "ChinaBoundaryTools", "CnemcAirTools",
]

# 必需依赖（arcpy 自带）与需额外安装的第三方库
REQUIRED_BUILTIN = ["numpy", "PIL", "scipy", "pandas", "requests"]
REQUIRED_THIRD = ["shapely"]


def check_module(name):
    try:
        mod = importlib.import_module(name)
        ver = getattr(mod, "__version__", "")
        return True, ver
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    print("=" * 60)
    print("GIS 工具箱合集 · 环境自检")
    print("=" * 60)

    ok = True

    print("\n[1] arcpy")
    try:
        import arcpy
        print(f"  OK  arcpy {arcpy.GetInstallInfo().get('Version', '')}")
    except Exception as e:
        print(f"  失败  {e}")
        ok = False

    print("\n[2] 内置依赖（ArcGIS Pro 自带）")
    for name in REQUIRED_BUILTIN:
        good, ver = check_module(name)
        print(f"  {'OK ' if good else '缺 '} {name} {ver}")
        if not good:
            ok = False

    print("\n[3] 第三方依赖（需手动安装）")
    for name in REQUIRED_THIRD:
        good, ver = check_module(name)
        print(f"  {'OK ' if good else '缺 '} {name} {ver}")
        if not good:
            ok = False
            print(f"      -> 安装：pip install {name}")

    print("\n[4] 8 个工具箱 .pyt 语法检查")
    for t in TOOLS:
        pyt = os.path.join(ROOT, t, t + ".pyt")
        if not os.path.exists(pyt):
            print(f"  缺   {t}/{t}.pyt")
            ok = False
            continue
        try:
            compile(open(pyt, encoding="utf-8").read(), pyt, "exec")
            print(f"  OK   {t}.pyt")
        except SyntaxError as e:
            print(f"  失败 {t}.pyt  ({e})")
            ok = False

    print("\n[5] 硬编码绝对路径残留检查")
    for t in TOOLS:
        pyt = os.path.join(ROOT, t, t + ".pyt")
        text = open(pyt, encoding="utf-8").read()
        if "城市研究领域Agent" in text or "github_arcgis_tools\\" in text:
            print(f"  残留 {t}.pyt")
            ok = False
    if not any("城市研究领域Agent" in open(os.path.join(ROOT, t, t + ".pyt"), encoding="utf-8").read()
               for t in TOOLS):
        print("  全部干净（无硬编码绝对路径）")

    print("\n" + "=" * 60)
    print("结果：", "全部就绪 ✅" if ok else "存在缺失，请按上述提示处理 ⚠️")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
