# -*- coding: utf-8 -*-
"""
netfix.py —— 网络信息功能修复（替换 app 模块里的 fetch_ipinfo / get_vpn_info）

重要约束
--------
本文件被编译后，其**顶层函数的 code object** 会被塞进 app 模块原有的
co_consts 里（按函数名替换），而 app 模块的字节码保持不变。因此：

  * 只能替换 app 里**已经存在**的函数名 —— 新增的顶层函数不会生成
    MAKE_FUNCTION 指令，永远不会被实例化，等于死代码。
    => 所有辅助函数一律写成 **嵌套函数**，由各自父函数的字节码创建。
  * 新 code object 的 **co_argcount 必须和原来一致**，因为模块里那条
    MAKE_FUNCTION 传入的默认值个数没变。给 fetch_ipinfo 加
    `force=False` 会让 `fetch_ipinfo()` 直接抛
    TypeError: missing 1 required positional argument。
    => 两个函数都保持零参数签名。

原实现的三个缺陷
----------------
1) 「出口 IP」其实是直连 IP（致命）
   `urllib.request.urlopen()` 用 `getproxies()`，而 CPython 里是：

       def getproxies():
           return getproxies_environment() or getproxies_registry()

   本机环境存在 `NO_PROXY`，于是 `getproxies_environment()` 返回
   `{'no': '127.0.0.1,localhost,::1'}` —— 非空 → 短路 →
   `getproxies_registry()` 根本不会被调用，注册表里的系统代理
   （127.0.0.1:7897）完全读不到，请求全部直连。

   实测：直连 = 36.1.78.24（中国 海南 电信）
        走代理 = 43.217.37.47（马来西亚 吉隆坡）
   原程序把前者标成「出口 IP」，via_proxy 恒为 false，
   「出口 IP ≠ 直连 IP」这条最关键的判据永远失效。

   修法：绕开 getproxies()，直接读注册表并显式构造 ProxyHandler。

2) /api/ipinfo 要 8.5 秒
   串行回退 + 直连 ip-api.com 必然 8s 超时，前端一直卡在「查询中…」。

   修法：出口/直连两路并发，每路内部多数据源并发竞速，
   超时降到 3.5s，实测 0.7s。

3) ipinfo / vpninfo 没有任何缓存，每次刷新都重跑 PowerShell。

   修法：加 15s 缓存（与 build_netinfo 同量级）。
"""


# --------------------------------------------------------------------------- #
def fetch_ipinfo():
    """出口 IP（经系统代理）+ 直连 IP（绕过代理），并发竞速 + 短缓存。"""

    # ---------------- 嵌套辅助（见模块 docstring 的约束说明）---------------- #
    def cache():
        g = globals()
        c = g.get("_NM_CACHE")
        if not isinstance(c, dict):
            c = {}
            g["_NM_CACHE"] = c
        return c

    def sys_proxy():
        """读 Windows 系统代理设置（HKCU 优先）。

        刻意不用 urllib.request.getproxies()：只要环境里有 NO_PROXY，
        它就只返回 {'no': ...}，注册表里的代理永远读不到。
        """
        out = {}
        try:
            import winreg
        except Exception:
            return out
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                k = winreg.OpenKey(
                    root, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            except Exception:
                continue
            try:
                def q(name):
                    try:
                        return winreg.QueryValueEx(k, name)[0]
                    except Exception:
                        return None
                try:
                    on = int(q("ProxyEnable") or 0)
                except Exception:
                    on = 0
                if on == 1:
                    srv = str(q("ProxyServer") or "").replace(" ", "").strip()
                    if srv:
                        if "=" in srv:
                            for part in srv.split(";"):
                                if "=" not in part:
                                    continue
                                a, b = part.split("=", 1)
                                a = a.strip().lower()
                                if a in ("http", "https") and b.strip():
                                    out.setdefault(a, b.strip())
                        else:
                            out.setdefault("http", srv)
                            out.setdefault("https", srv)
                ac = q("AutoConfigURL")
                if ac:
                    out.setdefault("pac", str(ac).strip())
            finally:
                try:
                    winreg.CloseKey(k)
                except Exception:
                    pass
        return out

    def norm(v):
        v = str(v or "").strip()
        if not v:
            return ""
        if "://" in v:
            return v
        if v.lower().startswith("socks"):
            return "socks5://" + v
        return "http://" + v

    def opener_for(proxies):
        if not proxies:
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        h = {}
        for scheme in ("http", "https"):
            v = norm(proxies.get(scheme))
            if v:
                h[scheme] = v
        if not h:
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener(urllib.request.ProxyHandler(h))

    def get(op, url, timeout):
        req = urllib.request.Request(url, headers={"User-Agent": "NetMonitor"})
        with op.open(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace").strip()

    def is_ip4(s):
        p = str(s or "").split(".")
        if len(p) != 4:
            return False
        for x in p:
            if not x.isdigit() or int(x) > 255:
                return False
        return True

    # 实测走代理：ip-api.com 0.64s / ip.sb 0.90s / ipinfo.io 429 限流
    # 实测直连  ：myip.ipip.net 0.40s / ip.3322.net 0.29s / ipify 10054
    EXIT_SOURCES = (
        ("ip-api.com",
         "http://ip-api.com/json/?lang=zh-CN&fields=status,message,country,"
         "countryCode,regionName,city,isp,org,as,proxy,hosting,query"),
        ("ip.sb", "https://api.ip.sb/geoip"),
        ("ipinfo.io", "https://ipinfo.io/json"),
    )
    DIRECT_SOURCES = (
        ("myip.ipip.net", "https://myip.ipip.net"),
        ("ip.3322.net", "https://ip.3322.net"),
        ("api.ipify.org", "https://api.ipify.org"),
    )

    def pick_json(name, body):
        d = json.loads(body)
        if not isinstance(d, dict):
            raise ValueError("bad json from %s" % name)
        if d.get("status") and d.get("status") != "success":
            raise ValueError("status=%s %s" % (d.get("status"), d.get("message") or ""))
        ip = str(d.get("query") or d.get("ip") or "").strip()
        if not is_ip4(ip):
            raise ValueError("no ip field from %s" % name)
        country = d.get("country") or ""
        region = d.get("regionName") or d.get("region") or ""
        city = d.get("city") or ""
        return {
            "ip": ip,
            "location": " ".join(str(x) for x in (country, region, city) if x),
            "country": str(country), "region": str(region), "city": str(city),
            "isp": str(d.get("isp") or d.get("organization") or d.get("org") or ""),
            "org": str(d.get("org") or d.get("organization") or d.get("as") or ""),
            "proxy": d.get("proxy"), "hosting": d.get("hosting"), "source": name,
        }

    def pick_text(name, body):
        # myip.ipip.net -> "当前 IP：36.1.78.24  来自于：中国 海南 电信"
        # ip.3322.net   -> "36.1.78.24"
        s = body
        for ch in (u"\uff1a", u"\uff0c", ":", ",", "\t", "\r", "\n"):
            s = s.replace(ch, " ")
        for tok in s.split():
            tok = tok.strip().strip(".")
            if is_ip4(tok):
                return {"ip": tok, "location": "", "country": "", "region": "",
                        "city": "", "isp": "", "org": "", "proxy": None,
                        "hosting": None, "source": name}
        raise ValueError("no ip in %s reply" % name)

    def race(sources, op, picker, timeout, budget):
        """并发竞速多数据源；错开 0/0.8/1.6s 启动，前面的命中就不打后面的。"""
        best = {}
        errs = []
        lock = threading.Lock()
        done = threading.Event()

        def one(name, url, delay):
            if delay and done.wait(delay):
                return
            try:
                got = picker(name, get(op, url, timeout))
            except Exception as e:
                with lock:
                    errs.append("%s: %s" % (name, str(e)[:90]))
                return
            with lock:
                if not best:
                    best.update(got)
                    done.set()

        for i, pair in enumerate(sources):
            threading.Thread(target=one, args=(pair[0], pair[1], i * 0.8),
                             daemon=True).start()
        done.wait(budget)
        with lock:
            return (dict(best) if best else None), list(errs)

    # ---------------- 缓存 ---------------- #
    c = cache()
    hit = c.get("ipinfo")
    if hit and (time.time() - hit[0]) < 15:
        out = dict(hit[1])
        out["cached"] = True
        return out

    t0 = time.time()
    proxies = sys_proxy()
    endpoint = norm(proxies.get("https") or proxies.get("http"))

    res = {"ip": "", "location": "", "country": "", "region": "", "city": "",
           "isp": "", "org": "", "proxy": None, "hosting": None,
           "direct_ip": "", "via_proxy": False, "source": "", "direct_source": "",
           "proxied": bool(endpoint), "proxy_endpoint": endpoint}

    slots = {}

    def job(key, sources, op, picker):
        slots[key] = race(sources, op, picker, 3.5, 9.0)

    ta = threading.Thread(target=job,
                          args=("exit", EXIT_SOURCES, opener_for(proxies), pick_json),
                          daemon=True)
    tb = threading.Thread(target=job,
                          args=("direct", DIRECT_SOURCES, opener_for({}), pick_text),
                          daemon=True)
    ta.start()
    tb.start()
    ta.join(12)
    tb.join(12)

    exit_res, exit_errs = slots.get("exit", (None, []))
    direct_res, direct_errs = slots.get("direct", (None, []))

    if exit_res:
        for k in ("ip", "location", "country", "region", "city",
                  "isp", "org", "proxy", "hosting", "source"):
            res[k] = exit_res[k]
    else:
        res["error"] = "出口 IP 获取失败（%s）" % (
            "；".join(exit_errs[:2]) or "无数据源响应")

    if direct_res:
        res["direct_ip"] = direct_res["ip"]
        res["direct_source"] = direct_res["source"]
    else:
        res["direct_err"] = "直连 IP 获取失败（%s）" % (
            "；".join(direct_errs[:2]) or "无数据源响应")

    if res["ip"] and res["direct_ip"]:
        res["via_proxy"] = res["ip"] != res["direct_ip"]

    res["elapsed_ms"] = int((time.time() - t0) * 1000)
    res["errors"] = (exit_errs + direct_errs)[:6]
    res["checked_at"] = int(time.time())
    res["cached"] = False
    c["ipinfo"] = (time.time(), dict(res))
    return res


# --------------------------------------------------------------------------- #
def get_vpn_info():
    """虚拟网卡 / Windows VPN 连接 / RAS 拨号 / 系统代理(含 PAC) / 环境变量代理。

    修复：vpninfo.ps1 要拉起 PowerShell（数秒），这里加 15s 缓存；
    系统代理改用直接读注册表，绕开 getproxies() 的 NO_PROXY 缺陷。
    """

    def cache():
        g = globals()
        c = g.get("_NM_CACHE")
        if not isinstance(c, dict):
            c = {}
            g["_NM_CACHE"] = c
        return c

    def sys_proxy():
        out = {}
        try:
            import winreg
        except Exception:
            return out
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                k = winreg.OpenKey(
                    root, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            except Exception:
                continue
            try:
                def q(name):
                    try:
                        return winreg.QueryValueEx(k, name)[0]
                    except Exception:
                        return None
                try:
                    on = int(q("ProxyEnable") or 0)
                except Exception:
                    on = 0
                if on == 1:
                    srv = str(q("ProxyServer") or "").replace(" ", "").strip()
                    if srv:
                        if "=" in srv:
                            for part in srv.split(";"):
                                if "=" not in part:
                                    continue
                                a, b = part.split("=", 1)
                                a = a.strip().lower()
                                if a in ("http", "https") and b.strip():
                                    out.setdefault(a, b.strip())
                        else:
                            out.setdefault("http", srv)
                            out.setdefault("https", srv)
                ac = q("AutoConfigURL")
                if ac:
                    out.setdefault("pac", str(ac).strip())
            finally:
                try:
                    winreg.CloseKey(k)
                except Exception:
                    pass
        return out

    def norm(v):
        v = str(v or "").strip()
        if not v:
            return ""
        if "://" in v:
            return v
        if v.lower().startswith("socks"):
            return "socks5://" + v
        return "http://" + v

    c = cache()
    hit = c.get("vpninfo")
    if hit and (time.time() - hit[0]) < 15:
        out = dict(hit[1])
        out["cached"] = True
        return out

    t0 = time.time()
    data = {"adapters": [], "vpns": [], "ras": [], "defaultRoute": None}
    try:
        if os.path.exists(_VPNINFO_PS1):
            p = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", _VPNINFO_PS1],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=134217728, timeout=25)
            txt = (p.stdout or "").strip()
            if txt:
                data = json.loads(txt)
    except Exception:
        pass

    adapters = data.get("adapters") or []
    vpns = data.get("vpns") or []
    ras = data.get("ras") or []
    dr = data.get("defaultRoute")

    vpn_adapters = [a for a in adapters if a.get("IsVpn")]
    connected_vpns = [v for v in vpns if str(v.get("Status", "")).lower() == "connected"]

    # ---- 系统代理（Clash / mihomo / v2rayN / sing-box 等）----
    sysp = sys_proxy()
    endpoint = norm(sysp.get("https") or sysp.get("http"))
    proxy = {"enabled": bool(endpoint or sysp.get("pac")),
             "server": norm(sysp.get("http") or sysp.get("https")),
             "endpoint": endpoint,
             "pac": sysp.get("pac") or "",
             "listening": False, "process": "", "env": ""}

    envs = []
    for nm in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
               "ALL_PROXY", "all_proxy"):
        v = os.environ.get(nm)
        if v and v not in envs:
            envs.append(v)
    if envs:
        proxy["env"] = ", ".join(envs)

    host, port = "", 0
    if endpoint:
        e2 = endpoint.split("://", 1)[-1].split("/")[0]
        if e2.count(":") == 1:
            host, _, ps = e2.partition(":")
            try:
                port = int(ps)
            except Exception:
                port = 0
    if host and port:
        try:
            sk = socket.create_connection((host, port), 0.4)
            sk.close()
            proxy["listening"] = True
        except Exception:
            proxy["listening"] = False
        if proxy["listening"]:
            try:
                for conn in psutil.net_connections(kind="tcp"):
                    try:
                        if conn.laddr and conn.laddr.port == port and conn.pid:
                            proxy["process"] = psutil.Process(conn.pid).name()
                            break
                    except Exception:
                        continue
            except Exception:
                pass

    has_proxy = bool(endpoint and proxy["listening"]) or bool(proxy["pac"]) or bool(proxy["env"])
    vpn_like = bool(vpn_adapters or connected_vpns or ras or (dr and dr.get("IsVpn")))
    connected = bool(vpn_like or has_proxy)
    mode = "vpn" if vpn_like else ("proxy" if has_proxy else "none")

    evidence = []
    for a in vpn_adapters:
        evidence.append("虚拟网卡: {0} ({1})".format(a.get("Name"), a.get("Desc")))
    for v in connected_vpns:
        evidence.append("系统 VPN 已连接: {0} -> {1}".format(v.get("Name"), v.get("Server")))
    for r in ras:
        evidence.append("拨号连接: {0} ({1})".format(r.get("Name"), r.get("Device")))
    if endpoint:
        note = ("，%s" % proxy["process"]) if proxy["process"] else ""
        if not proxy["listening"]:
            note += "，端口未监听（代理可能已关闭）"
        evidence.append("系统代理已启用: %s%s" % (endpoint, note))
    elif proxy["enabled"]:
        evidence.append("系统代理已启用（未配置服务器地址）")
    if proxy["pac"]:
        evidence.append("PAC 自动配置: %s" % proxy["pac"])
    if proxy["env"]:
        evidence.append("环境变量代理: %s" % proxy["env"])
    if not evidence and dr:
        evidence.append("默认出口网卡: %s" % dr.get("IfName"))

    if mode == "vpn":
        mode_label = "VPN 连接 / 虚拟网卡"
    elif mode == "proxy":
        if endpoint:
            mode_label = "系统代理（本地代理端口）"
        elif proxy["pac"]:
            mode_label = "系统代理（PAC 自动配置）"
        else:
            mode_label = "系统代理（环境变量）"
    else:
        mode_label = "直连"

    out = {"connected": connected, "mode": mode, "mode_label": mode_label,
           "adapters": vpn_adapters, "vpns": vpns, "ras": ras,
           "default_route": dr, "proxy": proxy, "evidence": evidence,
           "elapsed_ms": int((time.time() - t0) * 1000),
           "checked_at": int(time.time()), "cached": False}
    c["vpninfo"] = (time.time(), dict(out))
    return out

# --------------------------------------------------------------------------- #
def _watchdog():
    """看门狗：判断「前端窗口是否还在」，而不是「前端是否还在轮询」。

    原实现（就是这个 bug）
    ---------------------
        while True:
            time.sleep(3)
            if time.time() - _state['last_poll'] > 15:
                _shutdown(); return

    前端是 Edge 的 --app 窗口，1 秒轮询一次 /api/stats 来刷新 last_poll。
    窗口一旦被最小化，Chromium 会节流隐藏页面的定时器（隐藏几分钟后降到
    1 次/分钟，甚至直接冻结页面），轮询间隔必然超过 15 秒 —— 看门狗便认定
    窗口已关闭，调 os._exit(0) 把整个程序干掉。用户看到的现象就是：
    最小化几分钟后程序自己关停了。

    修法
    ----
    以「我们启动的那个 Edge 应用窗口进程是否还活着」为准：
      * 进程还在（最小化 / 隐藏都算）→ 绝不因轮询超时退出；
      * 进程消失（用户真关掉窗口）    → 连续两次确认（约 10s）后退出；
      * 完全找不到独立 profile 的 Edge（回退用系统默认浏览器打开）→
        退回轮询超时判断，阈值从 15s 放宽到 10 分钟，且从看门狗启动起算，
        避免启动瞬间就被判死。
    """

    # 辅助函数必须是嵌套的：模块字节码只为原有顶层函数生成 MAKE_FUNCTION
    def edge_alive():
        """我们启动的 Edge(--app + 独立 edge-profile) 进程是否还在。

        返回 True / False；枚举失败返回 None（这一轮不作判断）。
        """
        try:
            for p in psutil.process_iter(["name", "cmdline"]):
                try:
                    nm = str(p.info.get("name") or "").lower()
                    if nm not in ("msedge.exe", "msedgewebview2.exe"):
                        continue
                    cl = " ".join(p.info.get("cmdline") or [])
                    if "edge-profile" in cl:
                        return True
                except Exception:
                    continue
        except Exception:
            return None
        return False

    t0 = time.time()
    seen = False      # 是否见过我们的 Edge 窗口进程
    miss = 0          # 连续多少次没看到
    while True:
        time.sleep(5)
        alive = edge_alive()
        if alive:
            seen = True
            miss = 0
            continue
        if alive is False:
            if seen:
                miss += 1
                if miss >= 2:
                    _shutdown()
                    return
            else:
                # 没走 Edge 独立窗口：退回轮询超时，但阈值放宽到 10 分钟
                ref = max(t0, _state.get("last_poll") or 0)
                if time.time() - ref > 600:
                    _shutdown()
                    return
