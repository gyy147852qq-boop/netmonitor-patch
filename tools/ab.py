# -*- coding: utf-8 -*-
"""
ab.py —— 旧实现 vs 新实现的对照实验。

    py -3.13 tools/ab.py A     # 环境里没有代理变量
    py -3.13 tools/ab.py B     # 环境代理指向已关闭端口
    py -3.13 tools/ab.py C     # 环境代理正常
    py -3.13 tools/ab.py A B C # 依次跑（每个场景会自动 fork 子进程）

为什么每个场景必须单开进程：urllib.request.urlopen 会把 opener 缓存进全局
_opener，第一次调用时的代理配置会一直沿用，同进程内改环境变量无效 —— 做代理
相关的对照实验时踩过这个坑。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import appload  # noqa: E402
import paths    # noqa: E402

SCENARIOS = {
    "A": ("环境里没有代理变量", {}),
    "B": ("环境代理指向已关闭端口 7899",
          {"HTTP_PROXY": "http://127.0.0.1:7899", "HTTPS_PROXY": "http://127.0.0.1:7899"}),
    "C": ("环境代理正常指向 7897",
          {"HTTP_PROXY": "http://127.0.0.1:7897", "HTTPS_PROXY": "http://127.0.0.1:7897"}),
}

VARIANTS = [
    ("V1", paths.SRC / "baseline_netfix_v1.py"),
    ("V2", paths.SRC / "netfix.py"),
]


def run_one(key: str) -> int:
    label, env = SCENARIOS[key]
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)
    os.environ.update(env)

    print("=" * 78)
    print("%s) %s   env HTTP_PROXY=%r" % (key, label, os.environ.get("HTTP_PROXY")))
    print("=" * 78)

    results = {}
    for tag, src in VARIANTS:
        if not src.exists():
            print("  %s: 缺少 %s" % (tag, src))
            continue
        mod = appload.load_app([src])
        t0 = time.time()
        try:
            d = mod.fetch_ipinfo()
            dt = time.time() - t0
            exit_ip = d.get("ip") or "-"
            direct = d.get("direct_ip") or "-"
            endpoint = d.get("proxy_endpoint") or "(空)"
            print("  %s: %5.2fs  出口=%-16s 直连=%-16s via_proxy=%-5s 端点=%s"
                  % (tag, dt, exit_ip, direct, d.get("via_proxy"), endpoint))
            results[tag] = (exit_ip, direct)
        except Exception as e:
            print("  %s: %.2fs 后抛异常: %r" % (tag, time.time() - t0, e))
            results[tag] = None
    if "V1" in results and "V2" in results:
        print("      -> 两者结果%s" % ("一致" if results["V1"] == results["V2"] else "不同"))
    print()
    return 0


def main(argv):
    keys = [a for a in argv[1:] if a.upper() in SCENARIOS] or list(SCENARIOS)
    if len(keys) > 1:
        for k in keys:                      # 每个场景独立子进程
            subprocess.run([sys.executable, str(Path(__file__)), k], check=False)
        return 0
    return run_one(keys[0].upper())


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))