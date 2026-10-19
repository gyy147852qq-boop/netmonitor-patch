# -*- coding: utf-8 -*-
"""
verify.py —— 按前端的方式调用 /api/netinfo、/api/ipinfo、/api/vpninfo 并断言结果。

    py -3.13 tools/verify.py              # 验证打了补丁的实现
    py -3.13 tools/verify.py --baseline   # 验证 src/baseline_netfix_v1.py（旧版）
    py -3.13 tools/verify.py --orig       # 不打任何补丁，原始实现

退出码 0 = 全部通过。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import appload  # noqa: E402
import paths    # noqa: E402


def main(argv):
    flags = [a for a in argv[1:] if a.startswith("--")]
    if "--orig" in flags:
        variant, patch_files = "原始实现（未打补丁）", []
    elif "--baseline" in flags:
        variant = "基线（上一版补丁 baseline_netfix_v1.py）"
        patch_files = [paths.SRC / "baseline_netfix_v1.py"]
    else:
        variant = "当前实现（src/netfix.py）"
        patch_files = [paths.SRC / "netfix.py"]

    print("=" * 72)
    print("VARIANT:", variant)
    print("=" * 72)

    mod = appload.load_app(patch_files)
    srv, port = appload.serve(mod)
    got = {}
    for p in ("/api/netinfo", "/api/ipinfo", "/api/vpninfo"):
        try:
            got[p] = appload.get_json(port, p)
        except Exception as e:
            got[p] = ({"_error": repr(e)}, 0.0)

    ni = got["/api/netinfo"][0]
    ip = got["/api/ipinfo"][0]
    vpn = got["/api/vpninfo"][0]
    px = vpn.get("proxy") or {}

    print("--- 本机 ---")
    print("  primary_ip : %s  (%s)   网卡 %d / 路由 %d"
          % (ni.get("primary_ip"), ni.get("hostname"),
             len(ni.get("adapters") or []), len(ni.get("routes") or [])))
    print("--- 出口 / 直连 ---")
    print("  出口 IP    : %s" % ip.get("ip"))
    print("  归属地     : %s" % ip.get("location"))
    print("  运营商     : %s" % ip.get("isp"))
    print("  直连 IP    : %s" % ip.get("direct_ip"))
    print("  via_proxy  : %s" % ip.get("via_proxy"))
    print("  查询走代理 : %s   端点 %s" % (ip.get("proxied"), ip.get("proxy_endpoint")))
    print("  数据源     : %s / %s" % (ip.get("source"), ip.get("direct_source")))
    print("  服务端耗时 : %s ms   HTTP %.2fs"
          % (ip.get("elapsed_ms"), got["/api/ipinfo"][1]))
    for e in (ip.get("errors") or [])[:4]:
        print("    未响应的源: %s" % e)
    print("--- VPN / 代理 ---")
    print("  connected=%s mode=%s (%s)"
          % (vpn.get("connected"), vpn.get("mode"), vpn.get("mode_label")))
    print("  代理       : endpoint=%s listening=%s process=%s"
          % (px.get("endpoint"), px.get("listening"), px.get("process")))
    for e in (vpn.get("evidence") or []):
        print("    依据: %s" % e)
    print("  HTTP %.2fs" % got["/api/vpninfo"][1])

    t0 = time.time()
    mod.fetch_ipinfo()
    print("  二次 fetch_ipinfo(): %.3fs（命中缓存应接近 0）" % (time.time() - t0))
    srv.shutdown()

    print("\n--- verdict ---")
    checks = [
        ("/api/netinfo 有数据", bool(ni.get("primary_ip"))),
        ("出口 IP 已取到", bool(ip.get("ip"))),
        ("直连 IP 已取到", bool(ip.get("direct_ip"))),
        ("ipinfo < 4s", got["/api/ipinfo"][1] < 4.0),
        ("vpninfo < 8s", got["/api/vpninfo"][1] < 8.0),
    ]
    if vpn.get("connected"):
        checks.append(("代理端点已识别", bool(px.get("endpoint"))))
    if ip.get("ip") and ip.get("direct_ip"):
        checks.append(("出口 IP ≠ 直连 IP（确实经代理）", ip["ip"] != ip["direct_ip"]))
    ok = True
    for name, passed in checks:
        print("  [%s] %s" % ("PASS" if passed else "FAIL", name))
        ok = ok and passed
    print("\nRESULT:", "ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))