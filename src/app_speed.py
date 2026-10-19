# -*- coding: utf-8 -*-
"""
app_speed.py —— 「一键测网速」后端补丁。

替换 app 模块里的两个顶层对象：
    fetch_public_ip  (argc=0)  —— 测速引擎；无测速命令时保持原公网 IP 探测
    Handler          (class)  —— 新增 /api/speedtest 路由，其余路由逐字保留

硬约束（见 tools/build.py 顶部说明）：
  * 本文件顶层的 import / 常量赋值永远不会执行 —— 一律写进函数体；
  * 顶层只许出现 app 里已存在的名字；
  * co_argcount 必须与原函数一致（fetch_public_ip 是 0）。

接口
----
  GET /api/speedtest            -> 当前状态（status）
  GET /api/speedtest?start=1    -> 启动一轮测速（异步，立即返回）
  GET /api/speedtest?cancel=1   -> 取消进行中的测速
"""


def fetch_public_ip():
    """有测速命令时跑测速；否则保持原有公网 IP 探测语义。"""

    HOST = "speed.cloudflare.com"
    PORT = 443
    DOWN_TPL = "http://speed.cloudflare.com/__down?bytes=%d"
    UP_URL = "https://speed.cloudflare.com/__up"
    SIZES = (1000000, 8000000, 32000000)
    UP_SIZE = 4000000
    TIMEOUT = 40.0
    UP_TIMEOUT = 25.0
    BUDGET = 75.0
    UA = {"User-Agent": "NetMonitor"}
    KEEP = ("phase", "progress", "started_at", "finished_at", "rtt_ms",
            "download_mbps", "upload_mbps", "upload_available", "up_note",
            "down_bytes", "up_bytes", "down_seconds", "up_seconds",
            "download_error", "upload_error", "ping_error", "error")

    # ------------- 命令分派：线程局部，避免并发请求串味 ------------- #
    def cmd_get():
        g = globals()
        tls = g.get("_SPD_TLS")
        if tls is None:
            import threading
            tls = threading.local()
            g["_SPD_TLS"] = tls
        return getattr(tls, "cmd", None), tls

    # ------------- 原有公网 IP 探测（保持语义） ------------- #
    def public_ip():
        import json as _json
        import urllib.request
        sources = [
            ("https://api.ipify.org?format=json", "json", "ip"),
            ("https://api.ip.sb/ip", "text", None),
            ("https://ipinfo.io/ip", "text", None),
            ("http://myip.ipip.net", "text", None),
        ]
        for url, kind, key in sources:
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=6) as r:
                    raw = r.read().decode("utf-8", "replace").strip()
                if kind == "json":
                    ip = _json.loads(raw).get(key, "")
                else:
                    ip = raw
                ip = ip.strip()
                first = ip.split("：")[-1].split()[0] if "IP" in ip else ip
                if _looks_like_ip(first):
                    return {"ip": first, "source": url, "raw": raw}
            except Exception:
                continue
        return {"ip": "", "error": "获取失败：网络不可用或被防火墙拦截。"}

    # ------------------------- 测速辅助 ------------------------- #
    def opener():
        import urllib.request
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def mbsp(nbytes, sec):
        if not sec or sec <= 0:
            return 0.0
        return round(nbytes * 8 / sec / 1e6, 2)

    def state():
        g = globals()
        s = g.get("_SPD_STATE")
        if not isinstance(s, dict):
            s = {"phase": "idle", "progress": "", "started_at": None,
                 "finished_at": None, "rtt_ms": None, "download_mbps": None,
                 "upload_mbps": None, "upload_available": None, "up_note": "",
                 "down_bytes": 0, "up_bytes": 0, "down_seconds": 0.0,
                 "up_seconds": 0.0, "download_error": None,
                 "upload_error": None, "ping_error": None, "error": None,
                 "cancel": False}
            g["_SPD_STATE"] = s
        return s

    def lock():
        g = globals()
        lk = g.get("_SPD_LOCK")
        if lk is None:
            import threading
            lk = threading.Lock()
            g["_SPD_LOCK"] = lk
        return lk

    def snap(s):
        out = {}
        for k in KEEP:
            v = s.get(k)
            if isinstance(v, float):
                v = round(v, 2)
            out[k] = v
        return out

    def ping(s):
        import socket
        import time
        best = None
        errs = []
        for _ in range(3):
            try:
                t0 = time.monotonic()
                c = socket.create_connection((HOST, PORT), timeout=6.0)
                c.close()
                rtt = (time.monotonic() - t0) * 1000.0
                if best is None or rtt < best:
                    best = rtt
            except Exception as e:
                errs.append(str(e))
        s["rtt_ms"] = round(best, 1) if best is not None else None
        s["ping_error"] = ";".join(errs[:2]) if errs else None

    def download(op, s, t0):
        import time
        import urllib.request
        err = None
        got = 0
        el = 0.0
        for want in SIZES:
            if s.get("cancel"):
                break
            if BUDGET - (time.monotonic() - t0) < 5.0:
                break
            try:
                req = urllib.request.Request(DOWN_TPL % want, headers=UA)
                t1 = time.monotonic()
                n = 0
                with op.open(req, timeout=TIMEOUT) as r:
                    while n < want:
                        if time.monotonic() - t0 > BUDGET:
                            break
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        n += len(chunk)
                if n > 0:
                    got, el, err = n, time.monotonic() - t1, None
                    if el >= 3.0:
                        break
            except Exception as e:
                err = str(e)
        s["down_bytes"] = got
        s["down_seconds"] = round(el, 2)
        s["download_mbps"] = mbsp(got, el) if got else None
        s["download_error"] = err

    def upload(op, s, t0):
        import time
        import urllib.request
        if s.get("cancel") or BUDGET - (time.monotonic() - t0) < 5.0:
            s["upload_available"] = False
            s["upload_mbps"] = None
            s["up_note"] = "时间预算不足，已跳过"
            return
        try:
            body = b"\x00" * UP_SIZE
            t1 = time.monotonic()
            req = urllib.request.Request(
                UP_URL, data=body, method="POST",
                headers={"User-Agent": "NetMonitor",
                         "Content-Type": "application/octet-stream"})
            with op.open(req, timeout=UP_TIMEOUT) as r:
                r.read()
            el = time.monotonic() - t1
            s["up_bytes"] = UP_SIZE
            s["up_seconds"] = round(el, 2)
            s["upload_mbps"] = mbsp(UP_SIZE, el)
            s["upload_available"] = True
            s["upload_error"] = None
            s["up_note"] = ""
        except Exception as e:
            s["upload_available"] = False
            s["upload_mbps"] = None
            s["up_bytes"] = 0
            s["upload_error"] = str(e)[:200]
            s["up_note"] = "TLS 上传通道不通，仅测下载"

    def run(s):
        import time
        op = opener()
        t0 = time.monotonic()
        try:
            s["progress"] = "正在测试延迟…"
            ping(s)
            s["progress"] = "正在测试下载速度…"
            download(op, s, t0)
            s["progress"] = "正在测试上传速度…"
            upload(op, s, t0)
            bad = bool(s.get("download_error")) and not s["down_bytes"]
            s["phase"] = "error" if bad else "done"
            s["progress"] = "失败" if bad else "完成"
        except Exception as e:
            s["phase"] = "error"
            s["progress"] = "失败"
            s["error"] = str(e)[:300]
        finally:
            s["finished_at"] = time.time()

    # ---------------------------- 分派 ---------------------------- #
    c, tls = cmd_get()
    tls.cmd = None
    if c is None or c not in ("start", "cancel", "status"):
        return public_ip()

    s = state()
    if c == "start":
        with lock():
            if s["phase"] == "running":
                return snap(s)
            s.update({"phase": "running", "progress": "正在测试延迟…",
                      "started_at": time.time(), "finished_at": None,
                      "rtt_ms": None, "download_mbps": None,
                      "upload_mbps": None, "upload_available": None,
                      "up_note": "", "down_bytes": 0, "up_bytes": 0,
                      "down_seconds": 0.0, "up_seconds": 0.0,
                      "download_error": None, "upload_error": None,
                      "ping_error": None, "error": None, "cancel": False})

        def worker():
            run(s)

        import threading
        threading.Thread(target=worker, daemon=True).start()
        return snap(s)

    if c == "cancel":
        with lock():
            if s["phase"] == "running":
                s["cancel"] = True
                s["progress"] = "正在取消…"
        return snap(s)

    return snap(s)


# =========================================================================== #
# Handler —— 保留全部原有路由，新增 /api/speedtest
# =========================================================================== #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8",
              cache="no-store"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        _state["last_poll"] = time.time()

        if path == "/api/stats":
            try:
                snap = _collector.snapshot(top=30)
                self._send(200, json.dumps(snap))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
            return

        if path == "/api/netinfo":
            try:
                force = "refresh" in urlparse(self.path).query
                self._send(200, json.dumps(build_netinfo(force=force)))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
            return

        if path == "/api/publicip":
            try:
                self._send(200, json.dumps(fetch_public_ip()))
            except Exception as e:
                self._send(500, json.dumps({"ip": "", "error": str(e)}))
            return

        # ------------------------ 新增：一键测速 ------------------------ #
        if path == "/api/speedtest":
            q = urlparse(self.path).query
            tls = globals().get("_SPD_TLS")
            if tls is None:
                import threading
                tls = threading.local()
                globals()["_SPD_TLS"] = tls
            if "cancel" in q:
                tls.cmd = "cancel"
            elif "start" in q:
                tls.cmd = "start"
            else:
                tls.cmd = "status"
            try:
                self._send(200, json.dumps(fetch_public_ip()))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
            return

        if path == "/api/ipinfo":
            try:
                self._send(200, json.dumps(fetch_ipinfo()))
            except Exception as e:
                self._send(200, json.dumps({"ip": "", "error": str(e)}))
            return
        if path == "/api/vpninfo":
            try:
                self._send(200, json.dumps(get_vpn_info()))
            except Exception as e:
                self._send(200, json.dumps({"connected": False,
                                            "error": str(e)}))
            return
        if path == "/api/icon":
            try:
                q = parse_qs(urlparse(self.path).query)
                p = (q.get("path") or [""])[0]
                png = extract_icon_png(p, 32)
                if png:
                    self._send(200, png, "image/png",
                               cache="public, max-age=86400")
                else:
                    self._send(404, b"", "image/png")
            except Exception:
                self._send(404, b"", "image/png")
            return
        if path == "/api/quit":
            self._send(200, json.dumps({"ok": True}))
            threading.Thread(target=_shutdown, daemon=True).start()
            return
        if path == "/" or path == "":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not fp.startswith(WEB_DIR) or not os.path.isfile(fp):
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(fp)[1].lower()
        try:
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, CT.get(ext, "application/octet-stream"))
        except Exception:
            self._send(500, "err", "text/plain; charset=utf-8")