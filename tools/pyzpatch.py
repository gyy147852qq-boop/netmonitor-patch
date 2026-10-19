# -*- coding: utf-8 -*-
"""
pyzpatch.py —— 往 PyInstaller 的 PYZ 归档里追加纯 Python 模块。

PYZ 格式（PyInstaller 5.x / 6.x 通用）：

    [0:4]   b"PYZ\x00"            magic
    [4:8]   python magic          与解释器匹配（如 313 -> f30d0d0a）
    [8:12]  toc_off (int32 BE)    TOC 在文件里的偏移
    [12:16] 0                     PyInstaller 6.x 起多出的 4 字节
    [17:..] 模块数据区，每项 = zlib.compress(marshal.dumps(code))
    [toc_off:] TOC = marshal(list[tuple[name, (typ, pos, length)]])

typ: 0 = 普通模块, 1 = 包(PACKAGE)。PACKAGE 项的内容是包的 code object。

追加策略
--------
PYZ 的 TOC 是整体 marshal，无法就地插入，只能：
    新PYZ = 原数据区[:toc_off]  +  新模块数据  +  marshal(原TOC + 新TOC项)
这样原有模块的 pos/length 完全不变，最大幅度降低风险。
"""

from __future__ import annotations

import marshal
import struct
import zlib

PYZ_MAGIC = b"PYZ\x00"


def parse_pyz(data: bytes):
    """-> (pyvers: bytes, toc: list, data_area: bytes)

    data_area 是不含 TOC 的前半部分（头部 + 模块数据）。
    """
    if data[:4] != PYZ_MAGIC:
        raise ValueError("不是 PYZ 归档：magic=%r" % data[:4])
    pyvers = data[4:8]
    toc_off = struct.unpack("!i", data[8:12])[0]
    try:
        toc = marshal.loads(data[toc_off:])
    except Exception:
        toc = marshal.loads(zlib.decompress(data[toc_off:]))
    return pyvers, toc, data[:toc_off]


def build_pyz(pyz: bytes, modules: dict) -> bytes:
    """把 modules（{模块名: code对象或源码str}）追加进 PYZ。

    modules 的值可以是：
      * code object（已编译）
      * str —— 会按给定文件名编译
    返回新的 PYZ 字节串。已存在的模块名会被跳过（幂等）。
    """
    pyvers, toc, area = parse_pyz(pyz)
    names = {t[0] for t in toc}

    add_toc = []
    body = bytearray(area)
    for name, payload in modules.items():
        if name in names:
            continue                      # 幂等：已存在则跳过
        if isinstance(payload, str):
            code = compile(payload, name.replace(".", "/") + ".py", "exec")
        else:
            code = payload
        blob = zlib.compress(marshal.dumps(code), 9)
        pos = len(body)
        body += blob
        # 顶层模块用 0；带点的名字如果对应包则用 1。这里统一用 0（PACKAGE
        # 只影响 import 时的语义，普通模块 0 即可）。
        add_toc.append((name, (0, pos, len(blob))))

    if not add_toc:
        return pyz

    new_toc = list(toc) + add_toc
    blob = marshal.dumps(new_toc)
    out = bytes(body) + blob
    out = (PYZ_MAGIC + pyvers
           + struct.pack("!i", len(body))
           + struct.pack("!i", 0)
           + out[16:])
    return out


def list_modules(pyz: bytes) -> list[str]:
    _, toc, _ = parse_pyz(pyz)
    return [t[0] for t in toc]