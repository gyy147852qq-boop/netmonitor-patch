# -*- coding: utf-8 -*-
"""
bootstrap.py —— 从一份 NetMonitor.exe 解出离线测试所需的参考树。

    py -3.13 tools/bootstrap.py                      # 用本机已安装的那份
    py -3.13 tools/bootstrap.py path/to/NetMonitor.exe

解出来的东西全在 .work/ 下（已被 .gitignore 排除）：

    .work/ref/app              app 脚本的 code object
    .work/ref/web_index.html   前端页面
    .work/ref/*.pyd *.dll      运行 app 字节码所需的原生依赖
    .work/mods/collector.pyc   PYZ 里的纯 python 模块

这样 verify / check_exe / ab 就能在不启动 exe 的前提下离线跑真实逻辑。
"""
from __future__ import annotations

import marshal
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402
from carchive import Archive  # noqa: E402

# 只有 app 直接/间接 import 的模块才需要从 PYZ 里取出来
NEEDED_MODULES = ("collector",)


def extract_ref_tree(archive: Archive, ref: Path) -> int:
    ref.mkdir(parents=True, exist_ok=True)
    n = 0
    for e in archive.entries:
        if e.typecode in ("b", "d", "m", "z", "s"):
            # 只有 app 运行时会 import 的才落盘，跳过 numpy/PIL 这类大依赖
            name = Path(e.key).name
            if e.typecode in ("b", "d") or name.endswith((".pyd", ".dll", ".ps1")):
                (ref / e.key.replace("/", "__")).write_bytes(archive.read(e.name))
                n += 1
    return n


def extract_pyz(archive: Archive, mods: Path) -> list[str]:
    """把 PYZ 里指定的模块解成 .pyc（zlib + marshal）。"""
    mods.mkdir(parents=True, exist_ok=True)
    pyz = archive.read("PYZ.pyz")          # read() 已经解过压，不要再解一次
    toc_off = struct.unpack("!i", pyz[8:12])[0]
    try:
        toc = marshal.loads(pyz[toc_off:])
    except Exception:
        toc = marshal.loads(zlib.decompress(pyz[toc_off:]))
    got = []
    for name, (_typ, pos, length) in toc:
        if name not in NEEDED_MODULES:
            continue
        code = marshal.loads(zlib.decompress(pyz[pos:pos + length]))
        target = mods / (name.replace(".", "/") + ".pyc")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            f.write(pyz[4:8])      # 与运行中的解释器匹配的 magic
            f.write(b"\0" * 12)     # 空的时间戳/大小字段
            marshal.dump(code, f)
        got.append(name)
    return got


def main(argv):
    exe = Path(argv[1]) if len(argv) > 1 else paths.BASE_EXE
    if not exe.exists():
        print("找不到 exe: %s\n请把路径作为参数传进来：" % exe)
        return 2
    print("source : %s (%d bytes)" % (exe, exe.stat().st_size))

    archive = Archive(exe)
    print("archive: %d entries, python %s, %s"
          % (len(archive.entries), archive.pylib, archive.pyvers))

    paths.ensure_work()
    # app code object
    (paths.REF / "app").write_bytes(archive.read("app"))
    # 前端页面
    (paths.REF / "web_index.html").write_bytes(archive.read("web/index.html"))
    n = extract_ref_tree(archive, paths.REF)
    mods = extract_pyz(archive, paths.MODS)

    print("wrote  : .work/ref/app, .work/ref/web_index.html, %d dependency files" % n)
    print("modules: %s" % (", ".join(mods) if mods else "(none)"))
    code = marshal.loads((paths.REF / "app").read_bytes())
    fns = sorted(c.co_name for c in code.co_consts
                 if hasattr(c, "co_code") and c.co_name not in ("<module>", "<genexpr>"))
    print("app    : %d top-level code objects, e.g. %s" % (len(fns), ", ".join(fns[:8])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))