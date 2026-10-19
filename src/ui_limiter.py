# -*- coding: utf-8 -*-
"""
ui_limiter.py —— 前端补丁：工具栏加「⚡ 下行限速」按钮 + 限速面板。

与 ui_speed.py 同模式，纯字符串替换，幂等。
"""

LIMITER_CSS = """  .lim-hero{display:flex;gap:26px;align-items:flex-end;padding-bottom:12px;
    border-bottom:1px solid var(--line);margin-bottom:12px;flex-wrap:wrap}
  .lim-item{min-width:110px}
  .lim-num{font-size:22px;font-weight:700;color:var(--up);font-variant-numeric:tabular-nums}
  .lim-num.lim-idle{color:var(--sub)}
  .lim-lbl{color:var(--sub);font-size:12px;margin-top:4px}
  .lim-sec{margin:14px 0}
  .lim-row{display:flex;gap:18px;flex-wrap:wrap;margin:10px 0}
  .lim-chk{display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px}
  .lim-inline{display:flex;gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap}
  .lim-inline input[type=number],.lim-inline input[type=text]{
    background:var(--bg);color:var(--txt);border:1px solid var(--line);
    border-radius:6px;padding:6px 9px;font-size:13px;width:120px}
  .lim-inline label{font-size:12.5px;color:var(--sub);display:flex;align-items:center;gap:4px}
  .lim-table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
  .lim-table th{text-align:left;color:var(--sub);font-weight:500;padding:5px 8px;
    border-bottom:1px solid var(--line);font-size:12px}
  .lim-table td{padding:6px 8px;border-bottom:1px solid #1e2740}
  .lim-mono{font-family:Consolas,"Courier New",monospace}
  .lim-del{background:#3a2320;border-color:#5a2530;color:#ffb4b4;padding:3px 9px;font-size:12px}
  .lim-presets{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
  .lim-presets button{font-size:12px;padding:5px 11px}
  .lim-err{color:var(--bad);font-size:12.5px;line-height:1.6;margin:8px 0}
  .lim-note{color:var(--sub);font-size:11.5px;line-height:1.7;margin-top:12px}"""

TOOLBAR_ANCHOR = '    <button onclick="showSpeedtest()">⚡ 一键测速</button>'
TOOLBAR_NEW = TOOLBAR_ANCHOR + "\n" + '    <button onclick="showLimiter()">🚦 限速</button>'

LIMITER_MODAL = """<div id="limModal" class="modal-overlay" onclick="if(event.target===this)closeLimiter()">
  <div class="modal">
    <div class="modal-head"><h3>⚡ 下行限速</h3><div class="spacer"></div>
      <button onclick="limApply()">应用</button>
      <button onclick="closeLimiter()">关闭</button></div>
    <div class="modal-body" id="limBody"><div class="empty">加载中…</div></div>
  </div>
</div>"""

LIMITER_JS = r"""function showLimiter(){
  document.getElementById("limModal").style.display="flex";
  limRefresh();
}
function closeLimiter(){document.getElementById("limModal").style.display="none";}

var _limTimer=null;
function limStopPoll(){ if(_limTimer){clearInterval(_limTimer); _limTimer=null;} }

function limFmtBps(bps){
  if(!bps) return "0";
  const u=["B/s","KB/s","MB/s","GB/s"];let i=0,v=bps;
  while(v>=1024&&i<u.length-1){v/=1024;i++;}
  return (i===0?v.toFixed(0):v.toFixed(1))+" "+u[i];
}
function limToMbps(v){
  const n=parseFloat(v);
  if(!n||isNaN(n)) return 0;
  if(document.getElementById("limUnitMb")&&document.getElementById("limUnitMb").checked)
    return Math.round(n*1000000);
  return Math.round(n*1024*1024);
}
function limFromBps(bps){
  const mb=document.getElementById("limUnitMb")&&document.getElementById("limUnitMb").checked;
  if(!bps) return "";
  return mb ? (bps/1000000).toFixed(2) : (bps/1048576).toFixed(2);
}

async function limFetch(q,body){
  try{
    const opt={cache:"no-store",method:body?"POST":"GET"};
    if(body){opt.headers={"Content-Type":"application/json"};opt.body=JSON.stringify(body);}
    const r=await fetch("/api/limiter"+(q||""),opt);
    if(!r.ok) throw new Error("HTTP "+r.status);
    return await r.json();
  }catch(e){ return {error:String(e.message||e)}; }
}

function limRender(d){
  const box=document.getElementById("limBody");
  if(!box) return;
  if(d.error){ box.innerHTML='<div class="empty">加载失败：'+esc(d.error)+'</div>'; return; }

  const st=d.st||{};
  const cfg=d.cfg||{};
  const on=!!cfg.enabled;
  const av=st.available!==false;
  let h="";

  // 状态条
  h+='<div class="lim-hero">';
  h+='<div class="lim-item"><div class="lim-num '+(on?"":"lim-idle")+'">'+(on?"已启用":"已关闭")+'</div><div class="lim-lbl">限速开关</div></div>';
  h+='<div class="lim-item"><div class="lim-num lim-idle">'+(st.rate?limFmtBps(st.rate):"0")+'</div><div class="lim-lbl">当前下行速率</div></div>';
  h+='<div class="lim-item"><div class="lim-num lim-idle">'+(st.dropped_pkts||0)+'</div><div class="lim-lbl">已丢弃包数</div></div>';
  h+='</div>';

  if(!av){
    h+='<div class="lim-err">驱动不可用：'+esc(st.error||"未知原因")+'</div>';
  }

  // 开关
  h+='<div class="lim-row"><label class="lim-chk"><input type="checkbox" id="limEnabled" '+(on?"checked":"")+' onchange="limApplyEnabled()"> 启用水印限速</label>';
  h+='<label class="lim-chk"><input type="checkbox" id="limScopeAll" '+(cfg.scope==="all"?"checked":"")+'> 含回环（限制走本机代理的流量）</label></div>';

  // 总限速
  h+='<div class="lim-sec"><div class="lim-lbl">总下行限速（留空或 0 = 不限）</div>';
  h+='<div class="lim-inline"><input type="number" id="limGlobal" min="0" step="0.1" placeholder="例如 30" value="'+(cfg.global?limFromBps(cfg.global):"")+'">';
  h+='<label><input type="radio" name="limUnit" id="limUnitMb" value="mb" checked> MB/s</label>';
  h+='<label><input type="radio" name="limUnit" value="kb"> KB/s</label></div></div>';

  // 规则表
  h+='<div class="lim-sec"><div class="lim-lbl">按进程限速规则</div>';
  h+='<table class="lim-table"><thead><tr><th>进程名</th><th>限速</th><th></th></tr></thead><tbody>';
  const rules=cfg.rules||{};
  const names=Object.keys(rules);
  if(!names.length){
    h+='<tr><td colspan="3" class="empty">暂无规则</td></tr>';
  } else {
    names.forEach(n=>{
      h+='<tr><td class="lim-mono">'+esc(n)+'</td><td>'+limFromBps(rules[n])+' MB/s</td>';
      h+='<td><button class="lim-del" onclick="limDelRule(\'"+esc(n)+"\')">删除</button></td></tr>';
    });
  }
  h+='</tbody></table></div>';

  // 新增规则
  h+='<div class="lim-sec"><div class="lim-lbl">新增规则</div>';
  h+='<div class="lim-inline"><input type="text" id="limNewName" placeholder="进程名，如 steam.exe" list="limProcList">';
  h+='<input type="number" id="limNewRate" min="0.1" step="0.1" placeholder="MB/s">';
  h+='<button onclick="limAddRule()">添加</button></div>';
  h+='<datalist id="limProcList"></datalist></div>';

  // 快捷预设
  h+='<div class="lim-sec"><div class="lim-lbl">快捷预设</div>';
  h+='<div class="lim-presets">';
  [["Steam 下载","steam.exe",5],["浏览器","msedge.exe",10],["所有下载","*",20]].forEach(p=>{
    h+='<button onclick="limPreset(\'"+p[1]+"\',"+p[2]+")">'+esc(p[0])+' · '+p[2]+'MB/s</button>';
  });
  h+='</div></div>';

  h+='<div class="lim-note">只限制<strong>下行（下载）</strong>流量。开启后 NetMonitor 会加载 WinDivert 驱动，';
  h+='按令牌桶放行/丢弃入站 TCP 包，不影响上行与其他程序。';
  h+='关闭所有规则时驱动自动卸载，零开销。</div>';

  box.innerHTML=h;
  limLoadProcs();
}

async function limLoadProcs(){
  try{
    const r=await fetch("/api/stats",{cache:"no-store"});
    const d=await r.json();
    const dl=document.getElementById("limProcList");
    if(!dl) return;
    const ps=(d.procs||d.processes||[]).map(p=>p.name).filter(Boolean);
    const uniq=[...new Set(ps)].sort();
    dl.innerHTML=uniq.map(n=>"<option value=\""+esc(n)+"\">").join("");
  }catch(e){}
}

function limPatch(){
  const p={};
  const en=document.getElementById("limEnabled");
  if(en) p.enabled=en.checked;
  const sc=document.getElementById("limScopeAll");
  if(sc) p.scope=sc.checked?"all":"wan";
  const g=document.getElementById("limGlobal");
  if(g&&g.value) p.global=limToMbps(g.value);
  return p;
}

async function limApplyEnabled(){
  await limFetch("",limPatch());
  limRefresh();
}

async function limApply(){
  await limFetch("",limPatch());
  limRefresh();
}

async function limAddRule(){
  const n=(document.getElementById("limNewName").value||"").trim().toLowerCase();
  const v=document.getElementById("limNewRate").value;
  if(!n||!v){ return; }
  const p=limPatch();
  p.rules={};
  p.rules[n]=limToMbps(v);
  await limFetch("",p);
  limRefresh();
}

async function limDelRule(n){
  const p=limPatch();
  p.rules={};
  p.rules[n]=0;
  await limFetch("",p);
  limRefresh();
}

function limPreset(name,mbps){
  if(name==="*"){
    document.getElementById("limEnabled").checked=true;
    document.getElementById("limGlobal").value=String(mbps);
    limApply();
  } else {
    document.getElementById("limEnabled").checked=true;
    document.getElementById("limNewName").value=name;
    document.getElementById("limNewRate").value=String(mbps);
    limAddRule();
  }
}

async function limRefresh(){
  limStopPoll();
  const d=await limFetch("");
  limRender(d);
  if(d.cfg&&d.cfg.enabled&&d.st&&d.st.available!==false){
    _limTimer=setInterval(async()=>{
      const s=await limFetch("");
      if(s.st&&!s.error){
        const el=document.getElementById("limRateNow");
        if(el) el.textContent=limFmtBps(s.st.rate);
      }
    },1000);
  }
}"""


def patch_limiter(html: str) -> str:
    """把限速按钮 + 面板 + JS 注入 index.html。幂等。"""
    if "showLimiter" in html and 'id="limModal"' in html:
        return html

    # 1) 样式
    anchor = "  .sp-progress{color:var(--sub);font-size:12.5px;margin:0 0 2px}\n"
    if anchor in html:
        html = html.replace(anchor, anchor + LIMITER_CSS, 1)
    elif "  .ni-kv .ni-val{font-size:15px}\n" in html:
        html = html.replace("  .ni-kv .ni-val{font-size:15px}\n",
                            "  .ni-kv .ni-val{font-size:15px}\n" + LIMITER_CSS, 1)
    else:
        assert "</style>" in html, "style anchor missing"
        html = html.replace("</style>", LIMITER_CSS + "\n</style>", 1)

    # 2) 工具栏按钮
    assert TOOLBAR_ANCHOR in html, "toolbar anchor not found"
    html = html.replace(TOOLBAR_ANCHOR, TOOLBAR_NEW, 1)

    # 3) 弹窗
    assert "</body>" in html, "body end missing"
    html = html.replace("</body>", "\n" + LIMITER_MODAL + "\n</body>", 1)

    # 4) JS（放到 initCharts(); 之前，确保 esc() 已定义）
    tail = "initCharts();\npoll();\nsetInterval(poll,1000);"
    assert tail in html, "script tail anchor not found"
    html = html.replace(tail, LIMITER_JS + "\n" + tail, 1)
    return html