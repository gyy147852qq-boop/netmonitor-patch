def get_vpn_info():
    """VPN / 代理检测：虚拟网卡、Windows VPN 连接、RAS 拨号、系统代理(含 PAC)、环境变量代理。"""
    data = {"adapters": [], "vpns": [], "ras": [], "defaultRoute": None}
    try:
        if os.path.exists(_VPNINFO_PS1):
            p = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", _VPNINFO_PS1],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=134217728, timeout=25)
            out = (p.stdout or "").strip()
            if out:
                data = json.loads(out)
    except Exception:
        pass

    adapters = data.get("adapters") or []
    vpns = data.get("vpns") or []
    ras = data.get("ras") or []
    dr = data.get("defaultRoute")

    vpn_adapters = [a for a in adapters if a.get("IsVpn")]
    connected_vpns = [v for v in vpns if str(v.get("Status", "")).lower() == "connected"]

    # ---- 系统代理（Clash / mihomo / v2rayN / sing-box 等本地代理软件的系统代理模式）----
    proxy = {"enabled": False, "server": "", "endpoint": "", "pac": "",
             "listening": False, "process": "", "env": ""}
    try:
        import winreg
        vals = {}
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                k = winreg.OpenKey(root, "Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings")
            except Exception:
                continue
            for nm in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
                try:
                    v = winreg.QueryValueEx(k, nm)[0]
                    if not vals.get(nm):
                        vals[nm] = v
                except Exception:
                    pass
            try:
                winreg.CloseKey(k)
            except Exception:
                pass
        if int(vals.get("ProxyEnable") or 0) == 1:
            proxy["enabled"] = True
        proxy["server"] = str(vals.get("ProxyServer") or "")
        proxy["pac"] = str(vals.get("AutoConfigURL") or "")
    except Exception:
        pass

    ep = ""
    for part in str(proxy["server"]).replace(" ", "").split(";"):
        if not part:
            continue
        if "=" in part:
            k2, v2 = part.split("=", 1)
            if k2.lower() in ("http", "https") and not ep:
                ep = v2
        elif not ep:
            ep = part
    proxy["endpoint"] = ep

    envs = []
    for nm in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v3 = os.environ.get(nm)
        if v3 and v3 not in envs:
            envs.append(v3)
    if envs:
        proxy["env"] = ", ".join(envs)

    host, port = "", 0
    if ep:
        e2 = ep.split("://", 1)[-1].split("/")[0]
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
                for c in psutil.net_connections(kind="tcp"):
                    try:
                        if c.laddr and c.laddr.port == port and c.pid:
                            proxy["process"] = psutil.Process(c.pid).name()
                            break
                    except Exception:
                        continue
            except Exception:
                pass

    proxy_ok = bool(proxy["enabled"] and (not ep or proxy["listening"]))
    has_proxy = bool(proxy_ok or proxy["pac"] or proxy["env"])
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
    if proxy["enabled"] and ep:
        note = ""
        if proxy["process"]:
            note = "，{0}".format(proxy["process"])
        if not proxy["listening"]:
            note += "，端口未监听（代理可能已关闭）"
        evidence.append("系统代理已启用: {0}{1}".format(ep, note))
    elif proxy["enabled"]:
        evidence.append("系统代理已启用（未配置服务器地址）")
    if proxy["pac"]:
        evidence.append("PAC 自动配置: {0}".format(proxy["pac"]))
    if proxy["env"] and not (proxy["enabled"] and ep and ep in proxy["env"]):
        evidence.append("环境变量代理: {0}".format(proxy["env"]))
    if not evidence and dr:
        evidence.append("默认出口网卡: {0}".format(dr.get("IfName")))

    return {"connected": connected, "mode": mode, "adapters": vpn_adapters,
            "vpns": vpns, "ras": ras, "default_route": dr,
            "proxy": proxy, "evidence": evidence}


def fetch_ipinfo():
    """出口 IP + 归属地；同时探测直连 IP，用于判断是否走了代理出口。"""
    res = {"ip": "", "location": "", "country": "", "region": "", "city": "",
           "isp": "", "org": "", "proxy": None, "hosting": None, "source": ""}
    try:
        raw = _http_get("http://ip-api.com/json/?lang=zh-CN&fields=status,message,country,countryCode,regionName,city,lat,lon,isp,org,as,proxy,hosting,mobile,query")
        d = json.loads(raw)
        if d.get("status") != "success":
            raise ValueError("ip-api: " + str(d.get("message")))
        loc = " ".join([x for x in [d.get("country"), d.get("regionName"), d.get("city")] if x])
        res.update({"ip": d.get("query") or "", "location": loc,
                    "country": d.get("country") or "", "region": d.get("regionName") or "",
                    "city": d.get("city") or "", "isp": d.get("isp") or "",
                    "org": d.get("org") or d.get("as") or "",
                    "proxy": bool(d.get("proxy")), "hosting": bool(d.get("hosting")),
                    "source": "ip-api.com"})
    except Exception:
        try:
            d = json.loads(_http_get("https://ipinfo.io/json"))
            loc = " ".join([x for x in [d.get("country"), d.get("region"), d.get("city")] if x])
            res.update({"ip": d.get("ip") or "", "location": loc,
                        "country": d.get("country") or "", "region": d.get("region") or "",
                        "city": d.get("city") or "", "isp": d.get("org") or "",
                        "org": d.get("org") or "", "proxy": None, "hosting": None,
                        "source": "ipinfo.io"})
        except Exception:
            try:
                d = json.loads(_http_get("https://api.ip.sb/geoip"))
                loc = " ".join([x for x in [d.get("country"), d.get("region"), d.get("city")] if x])
                res.update({"ip": d.get("ip") or "", "location": loc,
                            "country": d.get("country") or "", "region": d.get("region") or "",
                            "city": d.get("city") or "",
                            "isp": d.get("organization") or d.get("isp") or "",
                            "org": d.get("organization") or "", "proxy": None,
                            "hosting": None, "source": "ip.sb"})
            except Exception:
                return {"ip": "", "error": "获取失败（可能无外网或接口不可达）"}

    # ---- 直连 IP（强制不走代理），用于判断代理出口 ----
    direct = ""
    for url in ("https://myip.ipip.net", "https://ip.3322.net", "https://api.ipify.org"):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(url, headers={"User-Agent": "NetMonitor"})
            with opener.open(req, timeout=4) as r:
                txt = r.read().decode("utf-8", "replace").strip()
            for tok in txt.replace("：", " ").replace(":", " ").replace("，", " ").replace(",", " ").split():
                if _looks_like_ip(tok):
                    direct = tok
                    break
            if direct:
                break
        except Exception:
            continue

    res["direct_ip"] = direct
    res["via_proxy"] = bool(direct and res.get("ip") and direct != res.get("ip"))
    return res
