# -*- coding: utf-8 -*-
"""paths.py —— 统一管理仓库路径与外部依赖位置，可用环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
RELEASE = ROOT / "release"
VENDOR = ROOT / "vendor"    # WinDivert 驱动文件（fetch_windivert.py 下载）
DOCS = ROOT / "docs"

# 所有中间产物都在这里，.gitignore 已排除
WORK = Path(os.environ.get("NBMON_WORK", ROOT / ".work"))

# 从原始 exe 解出来的参考树（bootstrap.py 生成）：
#   ref/app              app 脚本的 code object（marshal）
#   ref/web_index.html   前端页面
#   ref/collector.pyc    PYZ 里的 collector 模块（app 依赖它）
#   ref/*.pyd, *.dll ...  运行 app 代码所需的原生依赖
#   mods/                供 sys.path 使用的纯 python 模块
REF = WORK / "ref"
MODS = WORK / "mods"

# 基线 exe：默认取本机已安装的那一份，可用 NBMON_BASE 覆盖
DEFAULT_INSTALLED = Path(
    os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")
) / "NetMonitor" / "NetMonitor.exe"
BASE_EXE = Path(os.environ.get("NBMON_BASE", DEFAULT_INSTALLED))
INSTALLED_EXE = Path(os.environ.get("NBMON_INSTALLED", DEFAULT_INSTALLED))


def ensure_work() -> None:
    for d in (WORK, REF, MODS):
        d.mkdir(parents=True, exist_ok=True)


def missing_work() -> list[str]:
    """检查 bootstrap 是否跑过，返回缺失项列表。"""
    need = [REF / "app", REF / "web_index.html", REF / "vpninfo.ps1",
            MODS / "collector.pyc"]
    return [str(p.relative_to(WORK)) for p in need if not p.exists()]


def add_ref_to_syspath() -> None:
    """把解包出来的原生依赖挂到 sys.path，才能 exec app 的字节码。"""
    import sys
    for p in (str(MODS), str(REF)):
        if p not in sys.path:
            sys.path.insert(0, p)