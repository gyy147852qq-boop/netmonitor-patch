# -*- coding: utf-8 -*-
"""
check_exe.py —— 从「构建出来的 exe」里把 app 代码与 index.html 取出来实跑。

    py -3.13 tools/check_exe.py [dist/xxx.exe]

证明真正打进 exe 的就是我们要修的东西，而不是只验证了磁盘上的源码。
"""
from __future__ import annotations

import marshal
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import appload  # noqa: E402
import paths    # noqa: E402
from carchive import Archive  # noqa: E402

UI_CHECKS = [
    # 网络信息面板
    ("打开弹窗即自动查询", 'loadNetinfo(false); queryIpAndVpn();'),
    ("直连 IP 独立一行", 'id="directIp"'),
    ("出口 IP 带经代理标签", "经代理 · ≠ 直连 IP"),
    ("刷新按钮联动整块", 'onclick="refreshNetinfo()"'),
    ("请求带客户端超时", 'fetchWithTimeout("/api/ipinfo",20000)'),
    ("展示数据来源与耗时", "出口数据源"),
    ("旧的空态已移除", None),          # None = 必须不存在
    # 明细图 x 轴时间刻度
    ("明细图留出了标签高度", "grid:{left:48,right:14,top:22,bottom:32}"),
    ("明细图 x 轴标签已打开", 'axisLabel:{show:true,color:"#8ea0c0",fontSize:10,hideOverlap:true'),
    ("明细图 x 轴为 HH:MM:SS", "getHours().toString().padStart"),
    ("明细图 x 轴旧配置已移除", 'axisLabel:{show:false}'),
    ("top5 面板高度已调整", "#top5Panel{height:37%}"),
]


def check_html(html: str) -> bool:
    ok = True
    for name, needle in UI_CHECKS:
        if needle is None:
            passed = needle not in html
        else:
            passed = needle in html
        print("  [%s] %s" % ("PASS" if passed else "FAIL", name))
        ok = ok and passed
    return ok


def main(argv):
    rest = [a for a in argv[1:] if not a.startswith("--")]
    exe = Path(rest[0]) if rest else paths.ROOT / "dist" / "NetMonitor-fixed.exe"
    if not exe.exists():
        cands = sorted((paths.ROOT / "dist").glob("*.exe")) if (paths.ROOT / "dist").exists() else []
        print("找不到 %s" % exe)
        if cands:
            print("dist 下有：%s" % ", ".join(c.name for c in cands))
        return 2

    print("exe       : %s (%d bytes)" % (exe, exe.stat().st_size))
    archive = Archive(exe)
    code = marshal.loads(archive.read("app"))
    html = archive.read_text("web/index.html")

    fns = {c.co_name: c for c in code.co_consts if hasattr(c, "co_code")}
    print("app       : fetch_ipinfo %d / get_vpn_info %d bytes"
          % (len(fns["fetch_ipinfo"].co_code), len(fns["get_vpn_info"].co_code)))

    print("\n--- index.html ---")
    ok = check_html(html)

    print("\n--- 从 exe 里取出的代码实跑 ---")
    paths.ensure_work()
    (paths.REF / "app").write_bytes(archive.read("app"))
    (paths.REF / "vpninfo.ps1").write_bytes(archive.read("vpninfo.ps1"))
    mod = appload.load_app()
    srv, port = appload.serve(mod)
    ip, ip_dt = appload.get_json(port, "/api/ipinfo")
    vpn, vpn_dt = appload.get_json(port, "/api/vpninfo")
    srv.shutdown()

    px = vpn.get("proxy") or {}
    print("  出口 IP   : %s  (%s)" % (ip.get("ip"), ip.get("location")))
    print("  直连 IP   : %s" % ip.get("direct_ip"))
    print("  via_proxy : %s   端点 %s" % (ip.get("via_proxy"), ip.get("proxy_endpoint")))
    print("  来源/耗时 : %s / %s ms" % (ip.get("source"), ip.get("elapsed_ms")))
    print("  VPN 判定  : connected=%s mode=%s (%s)"
          % (vpn.get("connected"), vpn.get("mode"), vpn.get("mode_label")))
    print("  HTTP      : ipinfo %.2fs / vpninfo %.2fs" % (ip_dt, vpn_dt))

    print("\n--- verdict ---")
    for name, passed in [
        ("出口 IP 非空", bool(ip.get("ip"))),
        ("直连 IP 非空", bool(ip.get("direct_ip"))),
        ("出口 IP 走代理（与直连不同）", bool(ip.get("via_proxy"))),
        ("识别出代理进程", bool(px.get("process"))),
        ("ipinfo 服务端耗时 < 3s", (ip.get("elapsed_ms") or 9999) < 3000),
    ]:
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