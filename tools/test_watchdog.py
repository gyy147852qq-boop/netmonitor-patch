# -*- coding: utf-8 -*-
"""test_watchdog.py —— _watchdog 回归测试（最小化不再自杀 / 关窗仍能退出）。

    py -3.13 tools/test_watchdog.py                     # 测 dist/NetMonitor-fixed.exe
    py -3.13 tools/test_watchdog.py 某个.exe

做两件事：

A. 行为：把产物里真实的 `app` 字节码加载进来，用可控的 psutil 桩模拟
   「窗口进程还在 / 已消失」，跑真实的 `_watchdog`：
     * 窗口在 + 前端完全不轮询（等价于最小化被 Chromium 节流/冻结）→ 不能退出
     * 窗口进程消失（用户关窗）                                  → 约 10s 内退出
   同时拿已安装的旧版本对照，确认它能复现原 bug。

B. 真实进程识别：起一个本地页面 + 真实 Edge（--app + 独立 edge-profile），
   验证「窗口打开→检出 True」「关闭→检出 False」。
   注意：会短暂弹出一个小 Edge 窗口，约 1 分钟。

依赖 psutil（app 里也用它）。退出码 0 = 全部通过。
"""
from __future__ import annotations

import http.server
import marshal
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import paths  # noqa: E402

paths.add_ref_to_syspath()
from carchive import Archive  # noqa: E402

NEW = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "dist" / "NetMonitor-fixed.exe"
OLD = paths.BASE_EXE
ok = True


def load(exe: Path):
    """按 PyInstaller 的方式 exec 归档里的 app code object。"""
    code = marshal.loads(Archive(exe).read("app"))
    mod = types.ModuleType("appmod")
    mod.__file__ = "app.py"
    mod.__name__ = "appmod"          # 不触发 `if __name__ == "__main__"`
    exec(code, mod.__dict__)
    mod._VPNINFO_PS1 = str(paths.REF / "vpninfo.ps1")
    return mod


class _P:
    def __init__(self, name, cmdline):
        self.info = {"name": name, "cmdline": cmdline}


class _StubPsutil:
    """只实现 process_iter 的桩，模拟带 edge-profile 的 Edge 进程。"""

    def __init__(self):
        self.alive = True

    def process_iter(self, attrs=None):
        if not self.alive:
            return [_P("explorer.exe", ["explorer.exe"])]
        return [_P("explorer.exe", ["explorer.exe"]),
                _P("msedge.exe", ["msedge.exe", "--app=http://x/",
                                  "--user-data-dir=C:\\Temp\\QMNetMonitor\\edge-profile"])]


def case(mod, label, wait_seconds, expect_die):
    global ok
    fired = threading.Event()
    stub = _StubPsutil()
    mod._shutdown = lambda: fired.set()
    mod.psutil = stub
    mod._state["last_poll"] = 0          # 前端完全不轮询（最极端）
    threading.Thread(target=mod._watchdog, daemon=True).start()
    time.sleep(wait_seconds)
    if expect_die:
        stub.alive = False               # 模拟用户关掉窗口
        died = fired.wait(20)
    else:
        died = fired.is_set()
    print("  [%-22s] 退出=%s（期望 %s）"
          % (label, "是" if died else "否", "是" if expect_die else "否"))
    ok = ok and (died == expect_die)


def main():
    global ok
    print("=" * 72)
    print("A. 行为验证（新版本）:", NEW)
    if not NEW.exists():
        raise SystemExit("找不到产物：%s（先跑 tools/build.py）" % NEW)
    m = load(NEW)
    print("   _watchdog co_code =", len(m._watchdog.__code__.co_code), "bytes")
    case(m, "窗口在 / 完全不轮询 25s", 25, False)
    case(m, "关窗", 6, True)

    print("-" * 72)
    # 旧版对照：从安装目录的历史备份里找一份 _watchdog 与新版不同的（已装修复版时
    # 安装目录本身就是新版，不能当对照）。
    def watchdog_bytes(exe):
        code = marshal.loads(Archive(exe).read("app"))
        for k in code.co_consts:
            if isinstance(k, types.CodeType) and k.co_name == "_watchdog":
                return k.co_code
        return b""

    old_exe = None
    for bak in sorted(paths.INSTALLED_EXE.parent.glob(paths.INSTALLED_EXE.name + ".bak-*"),
                      reverse=True):
        try:
            if watchdog_bytes(bak) != m._watchdog.__code__.co_code:
                old_exe = bak
                break
        except Exception:
            continue
    if old_exe is None:
        print("A2. 跳过：安装目录里没有更早的版本备份可作对照")
    else:
        print("A2. 旧版本对照（应复现 bug）:", old_exe)
        mo = load(old_exe)
        print("   _watchdog co_code =", len(mo._watchdog.__code__.co_code), "bytes")
        case(mo, "窗口在 / 不轮询 8s", 8, True)

    print("=" * 72)
    print("B. 真实进程识别（真 Edge + 独立 edge-profile）")
    mb = load(NEW)
    ps = mb.psutil
    if ps is None:
        raise SystemExit("当前解释器没有 psutil，无法做真实进程测试：pip install psutil")

    def edge_alive():
        for p in ps.process_iter(["name", "cmdline"]):
            try:
                if str(p.info.get("name") or "").lower() not in ("msedge.exe", "msedgewebview2.exe"):
                    continue
                if "edge-profile" in " ".join(p.info.get("cmdline") or []):
                    return True
            except Exception:
                continue
        return False

    page = b"<html><body>watchdog probe</body></html>"

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    profile = os.path.join(tempfile.gettempdir(), "QMNetMonitor", "edge-profile-test")
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(edge):
        edge = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    subprocess.Popen([edge, "--app=http://127.0.0.1:%d/" % srv.server_address[1],
                      "--user-data-dir=%s" % profile,
                      "--window-size=420,260", "--window-position=120,120",
                      "--no-first-run", "--no-default-browser-check"])
    time.sleep(7)
    found = edge_alive()
    print("   Edge 窗口打开   -> 检测到 =", found, "(期望 True)")

    for p in ps.process_iter(["name", "cmdline"]):
        try:
            if "edge-profile-test" in " ".join(p.info.get("cmdline") or []):
                p.kill()
        except Exception:
            pass
    time.sleep(5)
    gone = not edge_alive()
    print("   关掉窗口后      -> 未检测到 =", gone, "(期望 True)")
    ok = ok and found and gone
    srv.shutdown()

    print("=" * 72)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
