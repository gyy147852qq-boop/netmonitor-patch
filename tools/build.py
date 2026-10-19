# -*- coding: utf-8 -*-
"""
build.py —— 把 src/ 下的修复打进 NetMonitor.exe。

    py -3.13 tools/build.py                      # 构建到 dist/ 并自检
    py -3.13 tools/build.py --install            # 自检通过后覆盖安装（先自动备份）
    py -3.13 tools/build.py --base 路径.exe       # 指定基座（默认本机已安装的那份）

原理
----
NetMonitor 是 PyInstaller 单文件，「后端」是归档里 `app` 这一个条目，内容是
Python code object（marshal）。所以修复 = 编译新的函数源码，把新函数对应的
code object 按名字替换进原 code object 的 co_consts，再把 `app` 和
`web/index.html` 两个条目重新压缩写回归档。

两条硬约束（违反会直接 TypeError / NameError，build 里有主动校验）：
  1. 只能替换**已存在**的顶层函数名。模块字节码不会为新名字生成 MAKE_FUNCTION，
     新增的顶层函数塞进 co_consts 也不会被实例化 —— 辅助逻辑必须写成嵌套函数。
  2. 新 code object 的 co_argcount 必须与原函数一致。模块里那条 MAKE_FUNCTION
     传的默认值个数没变，给函数加参数会直接抛
     TypeError: missing 1 required positional argument。
"""
from __future__ import annotations

import marshal
import shutil
import sys
import time
import types
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402

sys.path.insert(0, str(paths.SRC))     # ui / ui_axis 与 app 同为顶层模块

import ui as ui_mod  # noqa: E402
import ui_axis as axis_mod  # noqa: E402
import ui_speed as speed_ui_mod  # noqa: E402
from carchive import Archive  # noqa: E402

DIST = paths.ROOT / "dist"


# --------------------------------------------------------------------------- #
def splice(orig_code, new_mod):
    """按函数名把 new_mod 的顶层函数换进 orig_code。"""
    existing: dict[str, types.CodeType] = {}

    def index(c):
        for k in c.co_consts:
            if isinstance(k, types.CodeType):
                existing.setdefault(k.co_name, k)
                index(k)

    index(orig_code)

    mapping = {c.co_name: c for c in new_mod.co_consts if isinstance(c, types.CodeType)}

    added = sorted(set(mapping) - set(existing))
    if added:
        raise SystemExit(
            "src/ 里出现了 app 模块中不存在的顶层函数：%s\n"
            "新增顶层函数不会被实例化，请改成嵌套函数。" % ", ".join(added))

    print("replace   :", ", ".join(sorted(mapping)))
    report = []

    def walk(code):
        consts = []
        changed = False
        for c in code.co_consts:
            if isinstance(c, types.CodeType):
                if c.co_name in mapping:
                    if c.co_argcount != mapping[c.co_name].co_argcount:
                        raise SystemExit(
                            "%s 的参数个数从 %d 变成 %d，调用会直接 TypeError。"
                            % (c.co_name, c.co_argcount, mapping[c.co_name].co_argcount))
                    print("           %-14s %5d -> %5d bytes"
                          % (c.co_name, len(c.co_code), len(mapping[c.co_name].co_code)))
                    report.append(c.co_name)
                    consts.append(mapping[c.co_name])
                    changed = True
                    continue
                nc = walk(c)
                if nc is not c:
                    changed = True
                consts.append(nc)
            else:
                consts.append(c)
        return code.replace(co_consts=tuple(consts)) if changed else code

    return walk(orig_code), report


# --------------------------------------------------------------------------- #
def build(base_exe: Path, out_exe: Path):
    archive = Archive(base_exe)
    print("base      : %s (%d bytes, %d entries)"
          % (base_exe, base_exe.stat().st_size, len(archive.entries)))

    # --- 后端：替换 app 里的函数 ---
    orig_code = marshal.loads(archive.read("app"))
    report_all = []

    # 1) 网络信息修复
    src = (paths.SRC / "netfix.py").read_text(encoding="utf-8")
    patched, report = splice(orig_code, compile(src, "app.py", "exec"))
    report_all += report

    # 2) 一键测速（fetch_public_ip 承载引擎 + Handler 新增路由）
    src = (paths.SRC / "app_speed.py").read_text(encoding="utf-8")
    patched, report = splice(patched, compile(src, "app.py", "exec"))
    report_all += report

    if not report_all:
        raise SystemExit("没有任何函数被替换，中止")
    payloads = {"app": marshal.dumps(patched)}

    # --- 前端（幂等：基座已经打过补丁的步骤自动跳过）---
    raw_html = archive.read_text("web/index.html")
    html = raw_html
    if 'onclick="loadNetinfo(true)"' in html:
        # 基座还没打过网络信息面板补丁
        html = ui_mod.patch_html(html)
    if axis_mod.OLD_GRID in html:
        # 基座还没打过 x 轴刻度补丁
        html = axis_mod.patch_detail_axis(html)
    # 一键测速前端（幂等）
    html = speed_ui_mod.patch_speed(html)
    if html != raw_html:
        payloads["web/index.html"] = html.encode("utf-8")
        print("index.html: %d -> %d bytes"
              % (len(raw_html.encode("utf-8")), len(payloads["web/index.html"])))
    else:
        print("index.html: 基座已含全部前端补丁，跳过")

    out_exe.parent.mkdir(parents=True, exist_ok=True)
    written, changed = archive.write(out_exe, payloads)
    print("built     : %s (%d bytes)" % (written, written.stat().st_size))
    return written, changed


# --------------------------------------------------------------------------- #
def selfcheck(src_exe: Path, out_exe: Path, allowed={"app", "web/index.html"},
              expect=("app", "web/index.html")):
    import hashlib

    a, b = Archive(src_exe), Archive(out_exe)
    print("\n--- self check ---")
    assert [e.name for e in a.entries] == [e.name for e in b.entries], "条目顺序变了"
    print("entries   : %d -> %d，顺序一致" % (len(a.entries), len(b.entries)))

    changed = []
    for x, y in zip(a.entries, b.entries):
        rx, ry = a.read(x.name), b.read(y.name)
        if hashlib.sha256(rx).digest() != hashlib.sha256(ry).digest():
            changed.append((x.key, len(rx), len(ry)))
    print("changed   :", changed)

    extra = {c[0] for c in changed} - allowed
    assert not extra, "出现了意料之外的变更条目: %s" % sorted(extra)
    for name in expect:
        assert name in {c[0] for c in changed}, "%s 没有变化" % name
    assert a.data[:a.start] == b.data[:b.start], "引导段被改动"
    print("bootloader: 一致")
    try:
        assert a.read("PYZ.pyz") == b.read("PYZ.pyz"), "PYZ 被改动"
        print("PYZ       : 一致（%d bytes）" % len(a.read("PYZ.pyz")))
    except KeyError:
        print("PYZ       : 归档里没有该条目，跳过")


def install(out_exe: Path, target: Path):
    if not target.exists():
        raise SystemExit("安装目标不存在: %s" % target)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = target.with_suffix(target.suffix + ".bak-" + stamp)
    shutil.copy2(target, backup)
    shutil.copy2(out_exe, target)
    print("installed : %s" % target)
    print("backup    : %s" % backup)


# --------------------------------------------------------------------------- #
def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    base = Path(args[0]) if args else paths.BASE_EXE
    if not base.exists():
        print("基座 exe 不存在: %s\n用 NBMON_BASE 环境变量或第一个参数指定。" % base)
        return 2

    out_exe = DIST / ("%s-fixed.exe" % base.stem.replace(".fixed", ""))
    written, changed = build(base, out_exe)
    selfcheck(base, written, expect=[c[0] for c in changed])

    if "--install" in argv:
        install(written, paths.INSTALLED_EXE)
        print("\n重新启动程序生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))