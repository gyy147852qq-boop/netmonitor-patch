# -*- coding: utf-8 -*-
"""
ui.py —— 重写 NetMonitor「网络信息」面板的前端渲染与查询逻辑。

改动要点：
  * 打开面板即自动查询，不再停在「未查询 / 未检测」的空白态
  * 出口 IP 标注是否真的经过代理，直连 IP 单独一行，两者并列对比
  * 显式展示代理方式、数据来源、耗时、更新时间
  * 所有请求带客户端超时，任何数据源卡死都不会无限转圈
  * 任一数据源失败时把原因写进面板，而不是只显示一个破折号
"""

CSS_EXTRA = """  .ni-tag{font-size:11px;padding:1px 7px;border-radius:10px;margin-left:8px;
    border:1px solid var(--line);color:var(--sub);vertical-align:2px}
  .ni-tag.warn{color:var(--warn);border-color:#5a4526}
  .ni-tag.ok{color:var(--up);border-color:#20563f}
  .ni-foot{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;padding-top:10px;
    border-top:1px solid var(--line);color:var(--sub);font-size:11.5px}
  .ni-err{color:var(--bad);margin-top:6px;font-size:11.5px;line-height:1.6}
"""

NEW_JS = r'''function showNetinfo(){
  document.getElementById("netModal").style.display="flex";
  loadNetinfo(false); queryIpAndVpn();
}
function closeNetinfo(){document.getElementById("netModal").style.display="none";}
function refreshNetinfo(){loadNetinfo(true); queryIpAndVpn();}

async function fetchWithTimeout(url, ms){
  const ac=new AbortController();
  const t=setTimeout(()=>ac.abort(), ms);
  try{
    const r=await fetch(url,{cache:"no-store",signal:ac.signal});
    if(!r.ok) throw new Error("HTTP "+r.status);
    return await r.json();
  } finally {clearTimeout(t);}
}

async function loadNetinfo(force){
  const el=document.getElementById("niBody");
  if(force) el.innerHTML='<div class="empty">加载中…</div>';
  try{
    renderNetinfo(await fetchWithTimeout("/api/netinfo"+(force?"?refresh=1":""), 12000));
  }catch(e){
    el.innerHTML='<div class="empty">加载失败：'+esc(e.message||e)+'</div>';
  }
}

function renderNetinfo(d){
  const all=d.adapters||[];
  const act=all.filter(a=>a.up && a.ipv4 && !a.ipv4.startsWith("169.254") && !a.ipv4.startsWith("127."));
  let h="";
  h+=`<div class="ni-hero"><div><div class="ni-lbl">本机主 IP（默认出口）</div><div class="ni-big">${esc(d.primary_ip||"—")}</div></div>
      <div><div class="ni-lbl">主机名</div><div class="ni-mid">${esc(d.hostname||"—")}</div></div></div>`;
  h+=`<div class="ni-sec"><div class="ni-row"><span class="ni-lbl" style="margin:0">出口 IP / 归属地 / VPN / 代理</span>
      <button onclick="queryIpAndVpn()">查询</button></div>
      <div id="ipResult" style="margin-top:8px">
        <div class="ni-kv"><span class="ni-lbl">出口 IP</span><span id="pubIp" class="ni-val">查询中…</span><span id="pubTag"></span></div>
        <div class="ni-kv"><span class="ni-lbl">直连 IP</span><span id="directIp" class="ni-val">—</span><span id="directTag"></span></div>
        <div class="ni-kv"><span class="ni-lbl">归属地</span><span id="ipLoc" class="ni-val">—</span></div>
        <div class="ni-kv"><span class="ni-lbl">运营商</span><span id="ipIsp" class="ni-val">—</span></div>
        <div class="ni-kv"><span class="ni-lbl">VPN / 代理</span><span id="vpnStatus" class="ni-val">检测中…</span></div>
        <div class="ni-kv"><span class="ni-lbl">代理方式</span><span id="vpnMode" class="ni-val">—</span></div>
        <div id="vpnDetail" class="ni-note"></div>
        <div id="ipErrors" class="ni-err"></div>
        <div id="ipFoot" class="ni-foot"></div>
      </div>
      <div class="ni-note">出口 IP = 本机对外流量实际使用的 IP（经系统代理时为节点出口）；
        直连 IP = 强制绕过代理后的本机 IP。两者不同即表示流量已走代理。</div></div>`;
  h+=`<div class="ni-sec"><div class="ni-lbl">活动网卡</div><table class="ni-table"><thead><tr>
      <th>网卡</th><th>IPv4 / 掩码</th><th>MAC</th><th>速率</th></tr></thead><tbody>`;
  if(!act.length) h+=`<tr><td colspan="4" class="empty">无活动网卡</td></tr>`;
  act.forEach(a=>{h+=`<tr><td>${esc(a.nic)}</td><td class="ni-mono">${esc(a.ipv4||"")}${a.netmask?' / '+esc(a.netmask):''}</td><td class="ni-mono">${esc(a.mac||"—")}</td><td>${a.speed?a.speed+" Mbps":"—"}</td></tr>`;});
  h+=`</tbody></table></div>`;
  const v6=all.filter(a=>a.up && a.ipv6 && !a.ipv6.toLowerCase().startsWith("fe80"));
  if(v6.length){h+=`<div class="ni-sec"><div class="ni-lbl">IPv6（全球地址）</div><table class="ni-table"><tbody>`;
    v6.forEach(a=>{h+=`<tr><td>${esc(a.nic)}</td><td class="ni-mono">${esc(a.ipv6)}</td></tr>`;});h+=`</tbody></table></div>`;}
  const rt=d.routes||[];
  h+=`<div class="ni-sec"><div class="ni-lbl">网关 / DNS</div><table class="ni-table"><thead><tr>
      <th>适配器</th><th>默认网关</th><th>DNS</th></tr></thead><tbody>`;
  if(!rt.length) h+=`<tr><td colspan="3" class="empty">—</td></tr>`;
  rt.forEach(r=>{h+=`<tr><td>${esc(r.Description||("#"+r.Index))}</td><td class="ni-mono">${esc(r.Gateway||"—")}</td><td class="ni-mono">${esc(r.Dns||"—")}</td></tr>`;});
  h+=`</tbody></table></div>`;
  document.getElementById("niBody").innerHTML=h;
}

function _set(id,txt,color){const el=document.getElementById(id); if(el){el.textContent=txt; if(color)el.style.color=color;}}
function _tag(id,html){const el=document.getElementById(id); if(el) el.innerHTML=html||"";}
function _stamp(ms){
  if(!ms) return "—";
  const d=new Date(ms*1000);
  return d.toTimeString().slice(0,8);
}

async function queryIpAndVpn(){
  _set("pubIp","查询中…"); _tag("pubTag","");
  _set("directIp","查询中…"); _tag("directTag","");
  _set("ipLoc","…"); _set("ipIsp","…");
  _set("vpnStatus","检测中…"); _set("vpnMode","—");
  _set("vpnDetail",""); _tag("ipErrors",""); _tag("ipFoot","");

  const [ipr,vpr] = await Promise.allSettled([
    fetchWithTimeout("/api/ipinfo",20000).catch(e=>({error:String(e.message||e)})),
    fetchWithTimeout("/api/vpninfo",20000).catch(e=>({error:String(e.message||e)})),
  ]);

  const foot=[];
  let exitIp="", directIp="";

  if(ipr.status==="fulfilled" && ipr.value && !ipr.value.error){
    const d=ipr.value;
    exitIp=d.ip||""; directIp=d.direct_ip||"";
    _set("pubIp", exitIp || (d.error || "获取失败"));
    _set("directIp", directIp || "—");

    if(exitIp){
      _tag("pubTag", d.via_proxy
        ? '<span class="ni-tag warn">经代理 · ≠ 直连 IP</span>'
        : (directIp ? '<span class="ni-tag">与直连 IP 相同</span>' : ''));
    }
    if(directIp){
      _tag("directTag", d.via_proxy
        ? '<span class="ni-tag warn">被代理绕过</span>'
        : '<span class="ni-tag ok">当前出口</span>');
    }
    _set("ipLoc", d.location||"—");
    _set("ipIsp", d.isp||d.org||"—");
    if(d.source) foot.push("出口数据源 "+d.source);
    if(d.direct_source) foot.push("直连数据源 "+d.direct_source);
    if(typeof d.elapsed_ms==="number") foot.push("查询耗时 "+d.elapsed_ms+" ms");
    if(d.checked_at) foot.push("更新于 "+_stamp(d.checked_at));
    if(d.cached) foot.push("15 秒缓存");
    const errs=(d.errors||[]).slice();
    if(d.error) errs.unshift(d.error);
    if(d.direct_err) errs.push(d.direct_err);
    if(errs.length) _tag("ipErrors","部分数据源未响应："+errs.join("；"));
  } else {
    const msg = (ipr.status==="rejected") ? String(ipr.reason&&ipr.reason.message||ipr.reason)
              : ((ipr.value&&ipr.value.error)||"请求失败");
    _set("pubIp","失败"); _set("directIp","—");
    _set("ipLoc","—"); _set("ipIsp","—");
    _tag("ipErrors","出口 IP 查询失败："+esc(msg));
  }

  if(vpr.status==="fulfilled" && vpr.value && !vpr.value.error){
    const v=vpr.value;
    const el=document.getElementById("vpnStatus");
    const ev=(v.evidence||[]).slice();
    if(exitIp && directIp && exitIp!==directIp) ev.push("出口 "+exitIp+" ≠ 直连 "+directIp);
    if(v.connected){
      if(el){el.textContent="● 已连接 VPN / 代理"; el.style.color="var(--warn)";}
      _set("vpnMode", v.mode_label || (v.mode==="vpn"?"VPN 连接 / 虚拟网卡":"系统代理（本地代理端口）"));
      _set("vpnDetail", ev.length?("依据："+ev.join("；")):"（结合 IP 代理标志判断）","var(--sub)");
    }else{
      if(el){el.textContent="未检测到 VPN / 代理"; el.style.color="var(--up)";}
      _set("vpnMode","直连");
      const dr=v.default_route;
      _set("vpnDetail", dr?("默认出口网卡："+dr.IfName+(dr.Gateway?("（网关 "+dr.Gateway+"）"):"")):"", "var(--sub)");
    }
    if(v.cached) foot.push("VPN 检测 15 秒缓存");
  } else {
    const msg = (vpr.status==="rejected") ? String(vpr.reason&&vpr.reason.message||vpr.reason)
              : ((vpr.value&&vpr.value.error)||"请求失败");
    _set("vpnStatus","检测失败");
    const box=document.getElementById("ipErrors");
    if(box && box.innerHTML.indexOf("VPN 检测失败")<0){
      box.innerHTML += (box.innerHTML ? "<br>" : "") + "VPN / 代理检测失败："+esc(msg);
    }
  }
  _tag("ipFoot", foot.map(x=>"<span>"+esc(x)+"</span>").join(""));
}

'''


def patch_html(html: str) -> str:
    # 1) 追加少量样式
    anchor = '  .ni-kv .ni-val{font-size:15px}\n'
    assert anchor in html, "ni-kv style anchor missing"
    html = html.replace(anchor, anchor + CSS_EXTRA, 1)

    # 2) 弹窗头部的「刷新」按钮改为整块刷新
    old_btn = '<button onclick="loadNetinfo(true)">刷新</button>'
    assert old_btn in html, "refresh button missing"
    html = html.replace(old_btn, '<button onclick="refreshNetinfo()">刷新</button>', 1)

    # 3) 整块替换 showNetinfo .. initCharts 之间的脚本
    start = html.index("function showNetinfo(){")
    end = html.index("initCharts();", start)
    html = html[:start] + NEW_JS + html[end:]
    return html