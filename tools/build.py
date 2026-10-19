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
import ui_limiter as limiter_ui_mod  # noqa: E402
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

    # 3) 下行限速（_shutdown / Handler / main 三个都换）
    src = (paths.SRC / "app_extra.py").read_text(encoding="utf-8")
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
    # 下行限速前端（幂等）
    html = limiter_ui_mod.patch_limiter(html)
    if html != raw_html:
        payloads["web/index.html"] = html.encode("utf-8")
        print("index.html: %d -> %d bytes"
              % (len(raw_html.encode("utf-8")), len(payloads["web/index.html"])))
    else:
        print("index.html: 基座已含全部前端补丁，跳过")

    # --- 限速引擎：把 nb_limiter 追加进 PYZ ---
    # PYZ 的 TOC 是整体 marshal，不能就地插入，所以整块重打（见 pyzpatch.py）
    from pyzpatch import build_pyz, list_modules
    limiter_src = (paths.SRC / "limiter.py").read_text(encoding="utf-8")
    old_pyz = archive.read("PYZ.pyz")
    n_before = len(list_modules(old_pyz))
    new_pyz = build_pyz(old_pyz, {"nb_limiter": limiter_src})
    n_after = len(list_modules(new_pyz))
    if new_pyz != old_pyz:
        payloads["PYZ.pyz"] = new_pyz
        print("PYZ       : %d -> %d modules (+nb_limiter), %d -> %d bytes"
              % (n_before, n_after, len(old_pyz), len(new_pyz)))
    else:
        print("PYZ       : 已含 nb_limiter，跳过")

    # --- WinDivert 驱动文件：作为数据条目新增进 CArchive ---
    add = {}
    vend = paths.VENDOR
    for fname in ("WinDivert.dll", "WinDivert64.sys"):
        f = vend / fname
        if f.exists():
            add[fname] = f.read_bytes()
        else:
            print("WARN      : 缺少 %s，限速功能将不可用（跑 tools/fetch_windivert.py）" % fname)
    if add:
        print("vendor    : + %s" % ", ".join("%s (%d bytes)" % (k, len(v)) for k, v in add.items()))

    out_exe.parent.mkdir(parents=True, exist_ok=True)
    written, changed = archive.write(out_exe, payloads, add=add)
    print("built     : %s (%d bytes)" % (written, written.stat().st_size))
    return written, changed


# --------------------------------------------------------------------------- #
def selfcheck(src_exe: Path, out_exe: Path,
               allowed={"app", "web/index.html", "PYZ.pyz",
                        "WinDivert.dll", "WinDivert64.sys"},
               expect=("app", "web/index.html")):
    import hashlib

    a, b = Archive(src_exe), Archive(out_exe)
    print("\n--- self check ---")
    ka = [e.key for e in a.entries]
    kb = [e.key for e in b.entries]
    assert ka == kb[:len(ka)], "原有条目顺序变了"
    added = [k for k in kb if k not in set(ka)]
    print("entries   : %d -> %d（新增 %s）"
          % (len(a.entries), len(b.entries), ", ".join(added) or "无"))

    changed = []
    for x in a.entries:
        rx, ry = a.read(x.name), b.read(x.name)
        if hashlib.sha256(rx).digest() != hashlib.sha256(ry).digest():
            changed.append((x.key, len(rx), len(ry)))
    for k in added:
        changed.append((k, 0, len(b.read(k))))
    print("changed   :", changed)

    extra = {c[0] for c in changed} - allowed
    assert not extra, "出现了意料之外的变更条目: %s" % sorted(extra)
    for name in expect:
        assert name in {c[0] for c in changed}, "%s 没有变化" % name
    assert a.data[:a.start] == b.data[:b.start], "引导段被改动"
    print("bootloader: 一致")

    # PYZ 只允许新增模块，原有模块必须逐字节一致
    from pyzpatch import parse_pyz
    za = a.read("PYZ.pyz")
    zb = b.read("PYZ.pyz")
    # toc_off 在追加模块后必然后移，只校验 magic 与 python magic
    assert za[:8] == zb[:8], "PYZ 头部（magic / python magic）被改动"
    pa = dict((t[0], t[1]) for t in parse_pyz(za)[1])
    pb = dict((t[0], t[1]) for t in parse_pyz(zb)[1])
    for n, (typ, pos, length) in pa.items():
        q = pb[n]
        assert za[pos:pos + length] == zb[q[1]:q[1] + length], \
            "PYZ 模块 %s 被改动" % n
    print("PYZ       : %d -> %d modules，原有 %d 个逐字节一致"
          % (len(pa), len(pb), len(pa)))


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