# -*- coding: utf-8 -*-
"""
limiter.py —— 基于 WinDivert 的「下行限速」引擎（编译进 PYZ，模块名 nb_limiter）。

能力
----
  * 总下行限速：全局令牌桶，限制本机所有入站 TCP 流量。
  * 按进程限速：每个进程名一条规则，各自独立令牌桶。
  * 规则持久化到 %LOCALAPPDATA%\\NetMonitor\\limiter.json，重启后自动恢复。
  * 没有限速规则时 **不加载驱动**，零开销。

为什么必须上驱动
--------------
Windows 没有任何「不装驱动」的 API 能真正限制单进程的下载带宽：
qWAVE 只管出站且已废弃，QOS2 只作用于调用方自己的流，netsh 只能打 DSCP 标记。
只能在网络层收包后按令牌桶放行 / 丢包。

限速方向
--------
只做 **下行（下载）**：过滤器只抓 inbound 的 TCP，上行完全不经过本进程。

关于回环（本机代理）
------------------
本机浏览器流量走 verge-mihomo.exe (127.0.0.1:7897)。代理把数据回送给浏览器时，
入站包的 **目的端口是浏览器的本地端口**，端口->PID 的归属判断天然正确，
仍然算在 msedge.exe 头上。所以 scope='all'（含回环）才真正限得到走代理的下载。
scope='all' 时会额外排除本程序自己的 HTTP 服务端口，避免自己把自己限死。
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes

__all__ = ["start", "stop", "state", "apply", "dll_paths", "exclude_ports"]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
WINDIVERT_LAYER_NETWORK = 0

WINDIVERT_PARAM_QUEUE_LENGTH = 0
WINDIVERT_PARAM_QUEUE_TIME = 1

WINDIVERT_ADDRESS_SIZE = 64

FILTER_WAN = "inbound and tcp and ip and not loopback"
FILTER_ALL = "inbound and tcp and ip"

_MAX_PKT = 65535
_REFRESH_PORTMAP = 1.0          # 端口->PID 表刷新间隔（秒）
_REFRESH_NAMES = 5.0            # PID->进程名 缓存有效期（秒）
_PORTMAP_SLOW_MS = 250.0        # 刷新耗时超阈值则降频
_SAMPLE_INTERVAL = 0.5          # 速率采样间隔（秒）
_RATE_WINDOW = 1.2              # 速率统计窗口（秒）
_RATE_MAX = 20000               # 速率窗口最多保留的采样条数


def _res_dir() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return base
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _config_path() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "NetMonitor", "limiter.json")


# WinDivert 官方发行包的命名：64 位 DLL 叫 WinDivert.dll，驱动叫 WinDivert64.sys
# （早期文档写作 WinDivert64.dll，实际 zip 里并不存在这个名字）
DLL_NAME = "WinDivert.dll"
SYS_NAME = "WinDivert64.sys"


def dll_paths() -> dict:
    d = _res_dir()
    return {"dir": d,
            "dll": os.path.join(d, DLL_NAME),
            "sys": os.path.join(d, SYS_NAME)}


def _build_filter(scope: str, excluded) -> str:
    """按作用域 + 排除端口生成 WinDivert 过滤器。"""
    base = FILTER_ALL if scope == "all" else FILTER_WAN
    parts = [base]
    for p in sorted(excluded):
        if 0 < p < 65536:
            parts.append("not port = %d" % p)
    return " and ".join(parts)


# --------------------------------------------------------------------------- #
# 令牌桶
# --------------------------------------------------------------------------- #
class _Bucket:
    """允许突发 burst 字节、以 rate 字节/秒匀速回填的令牌桶。"""

    __slots__ = ("rate", "burst", "tokens", "ts")

    def __init__(self, rate: float, burst: float):
        self.rate = float(rate)
        self.burst = float(burst)
        self.tokens = float(burst)
        self.ts = time.monotonic()

    def reconfigure(self, rate: float, burst: float) -> None:
        self.rate = float(rate)
        self.burst = float(burst)
        if self.tokens > self.burst:
            self.tokens = float(burst)

    def take(self, n: int) -> bool:
        now = time.monotonic()
        if self.rate <= 0:
            self.tokens = self.burst
            self.ts = now
            return True
        dt = now - self.ts
        self.ts = now
        if dt > 0:
            self.tokens += dt * self.rate
            if self.tokens > self.burst:
                self.tokens = self.burst
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #
class _Limiter:
    def __init__(self):
        self.lock = threading.RLock()
        self.cfg = {"enabled": False, "scope": "all", "global": 0,
                    "burst_ms": 1000, "rules": {}}
        self.rules = {}                 # 与 cfg["rules"] 指向同一对象，原子换绑
        self.excluded = set()
        self.st = {
            "available": False, "error": "", "shaping": False,
            "passed_pkts": 0, "dropped_pkts": 0,
            "passed_bytes": 0, "dropped_bytes": 0,
            "rate": 0.0, "drop_rate": 0.0, "limiting": {},
            "handle_open": False, "lib": "",
        }
        self._win = None
        self._handle = None
        self._recv_thread = None
        self._map_thread = None
        self._stop = threading.Event()
        self._portmap = {}
        self._portmap_slow = False
        self._pid_name = {}
        self._name_ts = 0.0
        self._buckets = {}
        self._global_bucket = _Bucket(0, 65536)
        self._win_samples = []
        self._drop_samples = []
        self._rate = 0.0
        self._drop_rate = 0.0
        self._load_cfg()

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def _load_cfg(self) -> None:
        data = None
        try:
            with open(_config_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return
        cfg = self.cfg
        cfg["enabled"] = bool(data.get("enabled", cfg["enabled"]))
        if data.get("scope") in ("all", "wan"):
            cfg["scope"] = data["scope"]
        for key in ("global", "burst_ms"):
            try:
                cfg[key] = int(data.get(key, cfg[key]))
            except Exception:
                pass
        cfg["global"] = max(0, cfg["global"])
        cfg["burst_ms"] = min(10000, max(100, cfg["burst_ms"]))
        rules = data.get("rules")
        if isinstance(rules, dict):
            clean = {}
            for k, v in rules.items():
                try:
                    v = int(v)
                except Exception:
                    continue
                k = str(k).strip().lower()
                if k and v > 0:
                    clean[k] = v
            cfg["rules"] = clean
            self.rules = clean

    def _save(self) -> None:
        p = _config_path()
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # WinDivert
    # ------------------------------------------------------------------ #
    def _load_dll(self) -> bool:
        if self._win is not None:
            return True
        paths = dll_paths()
        try:
            win = ctypes.WinDLL(paths["dll"])
        except Exception as exc:
            self.st["available"] = False
            self.st["error"] = "加载 %s 失败：%s" % (DLL_NAME, exc)
            return False

        H = wintypes.HANDLE
        ULEN = ctypes.c_uint        # WinDivertRecv/Send 的 UINT*，不是 size_t

        # WinDivertOpen(filter, int layer, INT16 priority, UINT64 flags)
        # layer 是 int（4 字节），priority 才是 int16（2 字节）。
        # x64 上把 int 绑成 c_short 会让高位寄存器残留垃圾，layer 值错乱。
        win.WinDivertOpen.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                      ctypes.c_int16, ctypes.c_uint64]
        win.WinDivertOpen.restype = H
        win.WinDivertClose.argtypes = [H]
        win.WinDivertClose.restype = wintypes.BOOL
        win.WinDivertShutdown.argtypes = [H]
        win.WinDivertShutdown.restype = wintypes.BOOL
        # windivert.h: BOOL WinDivertRecv(HANDLE, PVOID pPacket, UINT packetLen,
        #                                 UINT *pRecvLen, PWINDIVERT_ADDRESS pAddr);
        # 只有 5 个参数；多写一个会让 ctypes 按 6 参调用，栈上错位，
        # 表现为开了限速就连不上网。
        win.WinDivertRecv.argtypes = [H, ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.POINTER(ULEN),
                                       ctypes.c_void_p]
        win.WinDivertRecv.restype = wintypes.BOOL
        # windivert.h: BOOL WinDivertSend(HANDLE, PCVOID pPacket, UINT packetLen,
        #                                 UINT *pSendLen, PCWINDIVERT_ADDRESS pAddr);
        win.WinDivertSend.argtypes = [H, ctypes.c_void_p, ULEN, ctypes.c_void_p,
                                       ctypes.POINTER(ULEN)]
        win.WinDivertSend.restype = wintypes.BOOL
        win.WinDivertSetParam.argtypes = [H, ctypes.c_int, ctypes.c_uint64]
        win.WinDivertSetParam.restype = wintypes.BOOL
        self._win = win
        self.st["available"] = True
        self.st["error"] = ""
        self.st["lib"] = paths["dll"]
        return True

    def _active(self) -> bool:
        return bool(self.cfg["enabled"]) and bool(self.cfg["global"] or self.rules)

    def _open(self) -> bool:
        """确保驱动句柄已打开。返回是否成功。"""
        with self.lock:
            if self._handle is not None:
                return True
            if not self._load_dll():
                return False
            filt = _build_filter(self.cfg["scope"], self.excluded)
            try:
                h = self._win.WinDivertOpen(filt.encode("ascii"),
                                            WINDIVERT_LAYER_NETWORK, 0, 0)
            except Exception as exc:
                self.st["error"] = "WinDivertOpen 失败：%s（需要管理员权限）" % (exc,)
                return False
            # WinDivert 2.x 的失败语义：返回 NULL **或** INVALID_HANDLE_VALUE(-1)。
            # 官方采用「惰性初始化」—— 即便权限不足也先返回 -1，
            # 真正的错误要等第一次 WinDivertRecv 才暴露。
            # 所以 `if not h` 这种判断对 -1 会失效（-1 是非零真值）。
            INVALID = 0xFFFFFFFFFFFFFFFF
            if h == 0 or h == INVALID:
                err = ctypes.get_last_error()
                # WinDivert 2.x 在权限不足时返回 INVALID_HANDLE_VALUE(-1)
                # 且**不设置** last error（实测 err=0）。此时没有任何错误码可读，
                # 只能靠"非管理员"这个已知条件给出可操作的提示。
                if h == INVALID and not err:
                    self.st["error"] = ("需要管理员权限：WinDivert 驱动无法打开。")
                else:
                    self.st["error"] = ("WinDivertOpen 失败（错误码 %d）。"
                                        "请尝试以管理员身份运行本程序。"
                                        % (err or 0))
                return False
            self._handle = h
            # 队列要足够大：包进了 WinDivert 队列但 Python 来不及收，
            # 队列满时 WinDivert 会**直接丢包**，表现为限速一开网络就卡死。
            # QUEUE_TIME 放大（毫秒），给收包循环留出处理时间。
            try:
                self._win.WinDivertSetParam(h, WINDIVERT_PARAM_QUEUE_LENGTH, 32768)
                self._win.WinDivertSetParam(h, WINDIVERT_PARAM_QUEUE_TIME, 2000)
            except Exception:
                pass
            self._buf = ctypes.create_string_buffer(_MAX_PKT)
            # 显式 cast 成 "B"（无符号字节）：
            # create_string_buffer 的 format 是 "c"，Python 3.13 起
            # 对 "c" 格式的 memoryview 切片/索引抛 NotImplementedError。
            self._mv = memoryview(self._buf).cast("B")
            # 与 WinDivertRecv 的 UINT *pPacketLen 对应（4 字节）
            self._plen = ctypes.c_uint(0)
            self._rflags = ctypes.c_uint(0)
            self._addr = ctypes.create_string_buffer(WINDIVERT_ADDRESS_SIZE)
            self._slen = ctypes.c_uint(0)
            self._bufp = ctypes.cast(self._buf, ctypes.c_void_p)
            self._plp = ctypes.byref(self._plen)
            self._rfp = ctypes.byref(self._rflags)
            self._ap = ctypes.byref(self._addr)
            self._slp = ctypes.byref(self._slen)
            self.st["handle_open"] = True
            self.st["error"] = ""
            return True

    def _close(self) -> None:
        """关闭句柄。

        关键：必须 WinDivertClose（不是只 Shutdown）。Shutdown 只是让 recv 返回，
        队列里剩余的包会被**全部丢弃**；只有 Close 才会把它们交还内核，
        否则关闭限速后网络仍然不通。
        """
        with self.lock:
            h = self._handle
            self._handle = None
            self.st["handle_open"] = False
        if h is not None:
            # Close 本身就会让阻塞中的 recv 返回，不需要先 Shutdown。
            # 先 Shutdown 反而会丢掉队列里待处理的包，造成关闭后网络假死。
            try:
                self._win.WinDivertClose(h)
            except Exception:
                try:
                    self._win.WinDivertShutdown(h)
                    self._win.WinDivertClose(h)
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # 端口 -> PID -> 进程名
    # ------------------------------------------------------------------ #
    def _refresh_portmap(self) -> None:
        import psutil
        t0 = time.monotonic()
        m = {}
        try:
            for c in psutil.net_connections(kind="tcp"):
                pid = c.pid
                la = c.laddr
                if pid and la and la.port:
                    m[la.port] = pid
        except Exception:
            return
        self._portmap = m
        self._portmap_slow = (time.monotonic() - t0) > _PORTMAP_SLOW_MS

    def _name_of(self, pid: int) -> str:
        now = time.monotonic()
        if now - self._name_ts < _REFRESH_NAMES:
            n = self._pid_name.get(pid)
            if n:
                return n
        try:
            import psutil
            n = psutil.Process(pid).name() or ""
        except Exception:
            n = ""
        if n:
            self._pid_name[pid] = n
            if len(self._pid_name) > 8192:
                self._pid_name = dict(list(self._pid_name.items())[-4096:])
        self._name_ts = now
        return n

    def _map_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh_portmap()
            except Exception:
                pass
            self._stop.wait(_REFRESH_PORTMAP * (4 if self._portmap_slow else 1))

    # ------------------------------------------------------------------ #
    # 收包 + 令牌桶
    # ------------------------------------------------------------------ #
    def _recv_loop(self) -> None:
        st = self.st
        # 这个线程从 start() 起必须常驻：用户随时可能通过 /api/limiter 打开限速。
        # 早期版本在 DLL 未加载时直接 return，导致线程永久退出，
        # 之后 apply(enabled=True) 再也没有人打开句柄，限速毫无效果。
        while not self._stop.is_set():
            # 没有任何限速规则时保持零开销：不加载驱动、不收包
            if not self._active():
                self._close()
                self._stop.wait(1.0)
                continue
            # 需要限速了：确保 DLL 已加载（可能还没加载过）
            win = self._win
            if win is None:
                self._load_dll()
                win = self._win
                if win is None:
                    self._stop.wait(2.0)
                    continue
            if not self._open():
                self._stop.wait(2.0)
                continue

            h = self._handle
            # rfp 已不需要：WinDivertRecv 的第 5 参是 pAddr 而不是 flags
            bufp, plp, ap, slp = (self._bufp, self._plp,
                                  self._ap, self._slp)
            mv = self._mv
            plen = self._plen
            recv = win.WinDivertRecv
            send = win.WinDivertSend
            monotonic = time.monotonic

            while not self._stop.is_set():
                try:
                    ok = recv(h, bufp, _MAX_PKT, plp, ap)
                except Exception:
                    ok = False
                if not ok:
                    break
                n = plen.value
                if n <= 0:
                    continue
                pkt = mv[:n]
                b0 = pkt[0]
                ver = b0 >> 4
                if ver == 4:
                    ihl = (b0 & 0x0F) << 2
                    if ihl < 20 or pkt[9] != 6:
                        self._send(h, bufp, n, ap, slp)
                        continue
                elif ver == 6:
                    if pkt[6] != 6:
                        self._send(h, bufp, n, ap, slp)
                        continue
                    ihl = 40
                else:
                    self._send(h, bufp, n, ap, slp)
                    continue

                dport = (pkt[ihl + 2] << 8) | pkt[ihl + 3]
                gb = self._global_bucket
                grate = gb.rate
                rules = self.rules

                # ---- 永远放行的包 ----
                # TCP flags 在 IP 头之后第 13 字节：SYN/FIN/RST 是握手与收尾，
                # 丢了会导致连接根本建立不起来（实测：限速一开就连不上网）。
                # ACK-only 的纯确认包通常很小，也一并放行，避免拖慢交互。
                flags = pkt[ihl + 13]
                if flags & 0x02 or flags & 0x10 or n < 64:
                    st["passed_pkts"] += 1
                    st["passed_bytes"] += n
                    self._send(h, bufp, n, ap, slp)
                    continue

                if not rules:
                    # 只按全局限速：不需要知道是谁
                    if grate > 0 and not gb.take(n):
                        self._drop(n, dport, None)
                        continue
                    st["passed_pkts"] += 1
                    st["passed_bytes"] += n
                    self._win_samples.append((monotonic(), n))
                    st["shaping"] = grate > 0
                    self._send(h, bufp, n, ap, slp)
                    continue

                # 需要按进程判定
                name = ""
                pid = self._portmap.get(dport)
                if pid:
                    name = self._name_of(pid)
                limit = self.rules.get(name.lower(), 0) if name else 0

                allow = True
                if limit > 0:
                    key = name.lower()
                    burst = max(float(limit),
                                self.cfg["burst_ms"] / 1000.0 * limit)
                    b = self._buckets.get(key)
                    if b is None:
                        b = _Bucket(limit, burst)
                        self._buckets[key] = b
                    elif b.rate != limit:
                        b.reconfigure(limit, burst)
                    allow = b.take(n)
                if allow and grate > 0:
                    allow = gb.take(n)

                if allow:
                    st["passed_pkts"] += 1
                    st["passed_bytes"] += n
                    self._win_samples.append((monotonic(), n))
                    st["shaping"] = True
                    self._send(h, bufp, n, ap, slp)
                else:
                    self._drop(n, dport, name or None)

            self._close()
            if not self._stop.is_set():
                self._stop.wait(0.5)

    def _send(self, h, bufp, n, ap, slp) -> None:
        """把包交还内核。

        句柄已被换掉/关闭时**不能直接丢包** —— 那等于把用户的流量吞了，
        表现为开启过限速之后网络一直不通。WinDivertClose 之后 send 会失败，
        这时只能依赖 Close 自己把残留包交还内核（见 _close 的注释）。
        """
        if self._handle is not h:
            return
        try:
            self._win.WinDivertSend(h, bufp, n, ap, slp)
        except Exception:
            pass

    def _drop(self, n: int, dport: int, name) -> None:
        st = self.st
        st["dropped_pkts"] += 1
        st["dropped_bytes"] += n
        self._drop_samples.append((time.monotonic(), n))
        lim = st["limiting"]
        key = name or ("port %d" % dport)
        lim[key] = lim.get(key, 0) + n
        if len(lim) > 128:
            for k in list(lim)[:64]:
                lim.pop(k, None)

    def _sample_loop(self) -> None:
        monotonic = time.monotonic
        while not self._stop.wait(_SAMPLE_INTERVAL):
            now = monotonic()
            cut = now - _RATE_WINDOW
            for store, attr in ((self._win_samples, "_rate"),
                                 (self._drop_samples, "_drop_rate")):
                i = 0
                while i < len(store) and store[i][0] < cut:
                    i += 1
                if i:
                    del store[:i]
                if len(store) > _RATE_MAX:
                    del store[:len(store) - _RATE_MAX // 2]
                setattr(self, attr, float(sum(x[1] for x in store)) / _RATE_WINDOW)
            if self._buckets and len(self._buckets) > 256:
                self._buckets = {k: v for k, v in self._buckets.items()
                                 if k in self.rules}

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        with self.lock:
            if self._recv_thread and self._recv_thread.is_alive():
                return
            self._stop.clear()
            self._recv_thread = threading.Thread(
                target=self._recv_loop, name="nb-limiter-recv", daemon=True)
            self._recv_thread.start()
            self._map_thread = threading.Thread(
                target=self._map_loop, name="nb-limiter-map", daemon=True)
            self._map_thread.start()
            threading.Thread(target=self._sample_loop, name="nb-limiter-sampler",
                             daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._close()
        t = self._recv_thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(2.0)
        self._recv_thread = None

    def apply(self, patch: dict) -> dict:
        changed_filter = False
        with self.lock:
            if "enabled" in patch:
                self.cfg["enabled"] = bool(patch["enabled"])
            if "scope" in patch:
                sc = patch["scope"]
                if sc in ("all", "wan") and sc != self.cfg["scope"]:
                    self.cfg["scope"] = sc
                    changed_filter = True
            if "global" in patch:
                try:
                    self.cfg["global"] = max(0, int(patch["global"]))
                except Exception:
                    pass
            if "burst_ms" in patch:
                try:
                    self.cfg["burst_ms"] = min(10000, max(100,
                                                          int(patch["burst_ms"])))
                except Exception:
                    pass
            if "rules" in patch and isinstance(patch["rules"], dict):
                clean = {}
                for k, v in patch["rules"].items():
                    try:
                        v = int(v)
                    except Exception:
                        continue
                    k = str(k).strip().lower()
                    if k and v > 0:
                        clean[k] = v
                self.cfg["rules"] = clean
                self.rules = clean          # 原子换绑，收包线程不会看到半个字典
            burst_s = self.cfg["burst_ms"] / 1000.0
            g = self.cfg["global"]
            self._global_bucket.reconfigure(g, max(float(g), burst_s * g, 65536.0))
            self._save()
        if changed_filter:
            self._close()
        return self.state()

    def exclude_ports(self, ports) -> None:
        new = set(int(p) for p in ports if p)
        with self.lock:
            if new != self.excluded:
                self.excluded = new
                self._close()              # 过滤器变了，重开

    def state(self) -> dict:
        with self.lock:
            cfg = dict(self.cfg)
            st = dict(self.st)
        st["limiting"] = dict(sorted(st["limiting"].items(),
                                     key=lambda kv: -kv[1])[:12])
        st["rate"] = self._rate
        st["drop_rate"] = self._drop_rate
        st.update({
            "enabled": cfg["enabled"],
            "active": bool(cfg["enabled"]) and bool(cfg["global"] or cfg["rules"]),
            "scope": cfg["scope"],
            "global": cfg["global"],
            "burst_ms": cfg["burst_ms"],
            "rules": dict(cfg["rules"]),
            "excluded": sorted(self.excluded),
            "filter": _build_filter(cfg["scope"], self.excluded),
            "pid_count": len(self._portmap),
        })
        st["dll"] = dll_paths()
        return st


_LIMITER = None
_INIT_LOCK = threading.Lock()


def _limiter() -> "_Limiter":
    global _LIMITER
    with _INIT_LOCK:
        if _LIMITER is None:
            _LIMITER = _Limiter()
        return _LIMITER


def start() -> None:
    try:
        _limiter().start()
    except Exception:
        pass


def stop() -> None:
    try:
        _limiter().stop()
    except Exception:
        pass


def apply(patch: dict) -> dict:
    return _limiter().apply(patch)


def state() -> dict:
    return _limiter().state()


def exclude_ports(ports) -> None:
    try:
        _limiter().exclude_ports(ports)
    except Exception:
        pass