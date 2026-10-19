# -*- coding: utf-8 -*-
"""
app_extra.py —— 替换 app 模块里的 `_shutdown` / `Handler` / `main` 三个顶层对象，
把「下行限速」功能接到现有 HTTP 接口与生命周期上。

替换规则（见 tools/build.py 顶部说明）：
  * 只能替换 app 里已存在的顶层名字。本文件顶层只有 `_shutdown` / `Handler` /
    `main` 三个，名字与原来完全一致；任何辅助逻辑都写成嵌套函数或类方法，
    否则模块字节码不会为新名字生成 MAKE_FUNCTION，等于死代码。
  * co_argcount 必须与原来一致：三者都是 0。
  * 不能引用 app 里不存在的新全局名。nb_limiter 单例按项目既有写法用
    globals() 惰性建出来（与 netfix.py 里的 _NM_CACHE 同一手法）。

新增接口
--------
  GET  /api/limiter      -> 限速状态（配置 + 实时统计）
  POST /api/limiter      -> 修改配置，body 为 JSON：
        {"enabled":bool,"global":<字节/秒，0=不限>,
         "scope":"all"|"wan","burst_ms":1000,
         "rules":{"chrome.exe":<字节/秒>}}
  GET  /api/limiter?...  -> 同 POST 的 querystring 形式（手工调试 / 应急）
  POST /api/limiter/off  -> 一键关掉全部限速

生命周期
--------
  main() 启动 nb_limiter 并注册 atexit；_shutdown() 先停限速再退出。
  本程序自己的 HTTP 端口会排除在过滤器之外 —— 否则开了总限速以后，
  前端轮询 /api/stats 自己也会被限，在极低速下界面会假死。
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


# ---------------- 生命周期 ----------------
def _shutdown():
    def lim():
        g = globals()
        m = g.get("_NBL")
        if m is None:
            import nb_limiter
            g["_NBL"] = m = nb_limiter
        return m

    try:
        _collector.stop()
    except Exception:
        pass
    try:
        lim().stop()
    except Exception:
        pass
    time.sleep(0.3)
    os._exit(0)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _lim(self):
        """取 nb_limiter 单例（写成方法，不占顶层名字）。"""
        g = globals()
        m = g.get("_NBL")
        if m is None:
            import nb_limiter
            g["_NBL"] = m = nb_limiter
        return m

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
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

    # ------------------------------------------------------------------ #
    def _limiter_state(self):
        try:
            self._send(200, json.dumps(self._lim().state()))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))

    def _limiter_apply(self, patch):
        try:
            st = self._lim().apply(patch)
            try:
                _log("limiter apply %s" % json.dumps(patch, ensure_ascii=False))
            except Exception:
                pass
            self._send(200, json.dumps(st))
        except Exception as e:
            try:
                _log_exc("limiter apply")
            except Exception:
                pass
            self._send(500, json.dumps({"error": str(e)}))

    def _speedtest(self, q):
        """一键测速：把命令塞进线程局部，再调 fetch_public_ip()。"""
        g = globals()
        tls = g.get("_SPD_TLS")
        if tls is None:
            import threading
            tls = threading.local()
            g["_SPD_TLS"] = tls
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

    def _patch_from_query(self, q):
        """querystring -> patch（手工调试 / 应急用）。"""

        def one(k, d=None):
            return (q.get(k) or [d])[0]

        patch = {}
        if "enabled" in q:
            patch["enabled"] = one("enabled") not in ("0", "false", "no", "")
        if "scope" in q:
            patch["scope"] = one("scope")
        for key in ("global", "burst_ms"):
            if key in q:
                try:
                    patch[key] = int(float(one(key, "0")))
                except Exception:
                    pass
        if "rules" in q:
            try:
                rules = json.loads(one("rules", "{}"))
                if isinstance(rules, dict):
                    patch["rules"] = rules
            except Exception:
                pass
        if "rule" in q and "name" in q:
            try:
                patch["rules"] = {one("name"): int(float(one("rule", "0")))}
            except Exception:
                pass
        return patch

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        if n <= 0 or n > 4 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except Exception:
            return {}

    # ------------------------------------------------------------------ #
    def do_POST(self):
        path = urlparse(self.path).path
        _state["last_poll"] = time.time()
        if path == "/api/limiter":
            body = self._read_body()
            if not isinstance(body, dict):
                body = {}
            # querystring 后合并：允许 ?enabled=1 覆盖 body
            body.update(self._patch_from_query(
                parse_qs(urlparse(self.path).query)))
            self._limiter_apply(body)
            return
        if path == "/api/limiter/off":
            self._limiter_apply({"enabled": False, "global": 0, "rules": {}})
            return
        # 一键测速：POST 形式（与 GET 等价，querystring 优先）
        if path == "/api/speedtest":
            q = parse_qs(urlparse(self.path).query)
            self._speedtest(q)
            return
        self._send(404, json.dumps({"error": "not found"}))

    def do_GET(self):
        path = urlparse(self.path).path
        _state["last_poll"] = time.time()

        if path == "/api/limiter":
            patch = self._patch_from_query(parse_qs(urlparse(self.path).query))
            if patch:
                self._limiter_apply(patch)
            else:
                self._limiter_state()
            return
        if path == "/api/limiter/off":
            self._limiter_apply({"enabled": False, "global": 0, "rules": {}})
            return

        # ---------------------- 一键测速 ---------------------- #
        # 引擎在 fetch_public_ip 里（app_speed.py 替换后的版本），
        # 命令通过线程局部 _SPD_TLS 传递，避免并发请求串味。
        if path == "/api/speedtest":
            self._speedtest(parse_qs(urlparse(self.path).query))
            return

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
                self._send(200, json.dumps({"connected": False, "error": str(e)}))
            return
        if path == "/api/icon":
            try:
                q = parse_qs(urlparse(self.path).query)
                p = (q.get("path") or [""])[0]
                png = extract_icon_png(p, 32)
                if png:
                    self._send(200, png, "image/png", cache="public, max-age=86400")
                else:
                    self._send(404, b"", "image/png")
            except Exception:
                self._send(404, b"", "image/png")
            return
        if path == "/api/quit":
            self._send(200, json.dumps({"ok": True}))
            threading.Thread(target=_shutdown, daemon=True).start()
            return
        # 静态文件
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


def main():
    def lim():
        g = globals()
        m = g.get("_NBL")
        if m is None:
            import nb_limiter
            g["_NBL"] = m = nb_limiter
        return m

    # 打包为 --windowed 后无控制台，sys.stdout/stderr 可能为 None，兜底到 devnull
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

    try:
        _log("main() start admin=%s" % _is_admin())
        _ensure_admin()
        _log("after ensure_admin")
        _collector.start()
        atexit.register(_collector.stop)

        # ---------------- 限速引擎 ----------------
        try:
            _log("limiter starting: %s" % lim().dll_paths())
            lim().start()
            atexit.register(lim().stop)
        except Exception:
            _log_exc("limiter start")

        port = _free_port()
        _state["port"] = port
        # 排除自己的 HTTP 端口：否则开了总限速，前端轮询自己也会被限
        try:
            lim().exclude_ports([port])
        except Exception:
            _log_exc("limiter exclude_ports")

        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        _state["server"] = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _log("http server on port %d" % port)

        try:
            pdir = os.path.join(tempfile.gettempdir(), "QMNetMonitor")
            os.makedirs(pdir, exist_ok=True)
            with open(os.path.join(pdir, "port.txt"), "w") as f:
                f.write(str(port))
        except Exception:
            _log_exc("write port.txt")

        threading.Thread(target=_watchdog, daemon=True).start()

        # 等采集器出第一轮数据再开窗口，避免启动瞬间显示全 0 像“坏了”
        _waited = 0.0
        while _waited < 10.0:
            time.sleep(0.5)
            _waited += 0.5
            if _collector.cycles >= 1 or _collector.error:
                break
        _open_window(port)
        _log("window opened")

        print("NetMonitor running at http://127.0.0.1:%d/  (admin=%s)"
              % (port, _is_admin()))
        # 阻塞主线程
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        _shutdown()
    except Exception:
        _log_exc("main")
        raise