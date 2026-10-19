# -*- coding: utf-8 -*-
"""
carchive.py —— PyInstaller CArchive 的最小读写实现（兼容 5.x / 6.x）。

CArchive = 一个带 cookie 的扁平归档，尾部 88 字节是 cookie：

    magic(8) pkg_len(4) toc_off(4) toc_len(4) pyvers(4) pylibname(64)

TOC 里每条记录：

    elen(4) offset(4) clen(4) ulen(4) cflag(1) typecode(1) name(elen-18 字节)

两个容易踩的坑：
  * 记录步长是 **elen**（整条记录含 18 字节头的总长），不是 18+elen
  * 取数据要用 **start + offset**，start = len(data) - pkg_len；直接用裸 offset
    会读错位置，zlib 解压直接报 "incorrect header check"
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
COOKIE = "!8sIIII64s"
ENTRY = "!iIIIBc"


class Entry:
    __slots__ = ("name", "offset", "clen", "ulen", "cflag", "typecode", "tocpos", "elen")

    def __init__(self, name, offset, clen, ulen, cflag, typecode, tocpos, elen):
        self.name = name
        self.offset = offset
        self.clen = clen
        self.ulen = ulen
        self.cflag = cflag
        self.typecode = typecode
        self.tocpos = tocpos
        self.elen = elen

    @property
    def key(self) -> str:
        """条目名的归一化形式（TOC 里是反斜杠）。"""
        return self.name.replace("\\", "/")

    def __repr__(self):
        return "<Entry %s type=%s %d/%d>" % (self.name, self.typecode, self.clen, self.ulen)


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        pos = self.data.rfind(MAGIC)
        if pos < 0:
            raise ValueError("不是 PyInstaller 单文件：找不到 CArchive cookie")
        (_m, self.pkg_len, self.toc_off, self.toc_len,
         self.pyvers, pylib) = struct.unpack(COOKIE, self.data[pos:pos + 88])
        self.pylib = pylib.rstrip(b"\x00").decode("ascii", "replace")
        self.start = len(self.data) - self.pkg_len
        self.cookie_pos = pos

        self.entries: list[Entry] = []
        p = self.start + self.toc_off
        while p + 18 <= pos:
            elen, offset, clen, ulen, cflag, tc = struct.unpack(ENTRY, self.data[p:p + 18])
            # elen 是整条记录的长度（含 18 字节头），所以名字只到第一个 NUL 为止，
            # 不能按 elen 长度切 —— 后面跟的是归档内容，不是 NUL 填充。
            end = self.data.index(b"\x00", p + 18)
            name = self.data[p + 18:end].decode("utf-8", "replace")
            self.entries.append(Entry(name, offset, clen, ulen, cflag,
                                      tc.decode("latin1"), p, elen))
            p += elen

    # ------------------------------------------------------------------ #
    def raw(self, e: Entry) -> bytes:
        return self.data[self.start + e.offset:self.start + e.offset + e.clen]

    def find(self, name: str) -> Entry:
        key = name.replace("\\", "/")
        for e in self.entries:
            if e.key == key:
                return e
        raise KeyError("条目不存在: %s" % name)

    def read(self, name: str) -> bytes:
        """取出条目内容（自动解压）。"""
        e = self.find(name)
        body = self.raw(e)
        return zlib.decompress(body) if e.cflag else body

    def read_text(self, name: str, encoding: str = "utf-8") -> str:
        return self.read(name).decode(encoding)

    def list_entries(self) -> list[tuple[str, str, int, int]]:
        return [(e.name, e.typecode, e.clen, e.ulen) for e in self.entries]

    # ------------------------------------------------------------------ #
    def write(self, out_path, replacements: dict[str, bytes]) -> Path:
        """把 replacements（键为归一化条目名）打进一个新的单文件 exe。

        除被替换的条目外，其余条目逐字节原样搬运；引导段与 TOC 记录头也保留，
        只更新 offset / clen / ulen / cflag 四个字段。
        """
        out = bytearray()
        toc = bytearray()
        changed = []

        for e in self.entries:
            if e.key in replacements:
                payload = replacements[e.key]
                blob = zlib.compress(payload, 9)
                body, clen, ulen, cflag = blob, len(blob), len(payload), 1
                changed.append((e.key, e.ulen, ulen))
            else:
                body = self.raw(e)
                clen, ulen, cflag = e.clen, e.ulen, e.cflag

            offset = len(out)
            out += body
            rec = bytearray(self.data[e.tocpos:e.tocpos + e.elen])
            struct.pack_into(ENTRY, rec, 0, e.elen, offset, clen, ulen,
                             cflag, e.typecode.encode())
            toc += rec

        toc_off = len(out)
        out += toc
        out += struct.pack(COOKIE, MAGIC, len(out) + 88, toc_off, len(toc),
                           self.pyvers, self.pylib.encode()[:64].ljust(64, b"\x00"))

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(self.data[:self.start] + bytes(out))
        return out_path, changed