# -*- coding: utf-8 -*-
"""
fetch_windivert.py —— 下载 WinDivert 驱动文件到 vendor/。

    py -3 tools/fetch_windivert.py

只取 x64 的 WinDivert.dll 与 WinDivert64.sys（程序是 64 位）。
官方发行包：https://github.com/basil00/WinDivert/releases
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

API = "https://api.github.com/repos/basil00/WinDivert/releases/latest"
KEEP = ("WinDivert.dll", "WinDivert64.sys")


def _get(url: str, timeout: int = 120) -> bytes:
    """直连失败则走系统代理（git 配置里已有 http.proxy）。"""
    last = None
    for proxies in ({}, {"http": "http://127.0.0.1:7897",
                          "https": "http://127.0.0.1:7897"}):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"})
            with op.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                                # noqa: BLE001
            last = e
    raise last


def main() -> int:
    rel = json.loads(_get(API, 30).decode("utf-8", "replace"))
    ver = rel["tag_name"].lstrip("v")
    zurl = [a["browser_download_url"] for a in rel["assets"]
            if a["name"] == "WinDivert-%s-A.zip" % ver][0]
    print("version : WinDivert %s" % ver)
    print("url     : %s" % zurl)

    blob = _get(zurl, 180)
    print("downloaded: %d bytes" % len(blob))
    z = zipfile.ZipFile(io.BytesIO(blob))

    paths.VENDOR.mkdir(parents=True, exist_ok=True)
    got = []
    for n in z.namelist():
        parts = n.replace("\\", "/").split("/")
        # 只要 <根>/x64/<文件>
        if len(parts) != 3 or parts[1] != "x64" or parts[2] not in KEEP:
            continue
        data = z.read(n)
        if data[:2] != b"MZ":
            print("skip %s: not a PE image" % parts[2])
            continue
        (paths.VENDOR / parts[2]).write_bytes(data)
        got.append((parts[2], len(data)))
        print("extracted: %-16s %9d bytes" % (parts[2], len(data)))

    if len(got) != len(KEEP):
        print("\n缺少文件：%s" % (set(KEEP) - {g[0] for g in got},))
        return 1

    (paths.VENDOR / "VERSION.txt").write_text(
        "WinDivert %s\nhttps://github.com/basil00/WinDivert\n" % ver,
        encoding="utf-8")
    print("\nok -> %s" % paths.VENDOR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())