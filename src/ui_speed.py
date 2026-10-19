# -*- coding: utf-8 -*-
"""
ui_speed.py —— 前端补丁：工具栏加「⚡ 一键测速」按钮 + 测速结果弹窗。

与 ui.py 同模式：纯字符串替换，作用在 web/index.html 上。
补丁幂等 —— 基座里已有标记时自动跳过。
"""

# 样式（插在 .ni-kv 样式之后）
SPEED_CSS = """  .sp-hero{display:flex;gap:26px;align-items:flex-end;padding-bottom:12px;
    border-bottom:1px solid var(--line);margin-bottom:12px;flex-wrap:wrap}
  .sp-item{min-width:132px}
  .sp-num{font-size:34px;font-weight:700;font-variant-numeric:tabular-nums;
    line-height:1.1;color:var(--down)}
  .sp-num.sp-up{color:var(--up)}
  .sp-num.sp-rtt{color:var(--txt);font-size:28px}
  .sp-num.sp-idle{color:var(--sub);font-size:24px}
  .sp-unit{font-size:13px;color:var(--sub);margin-left:4px;font-weight:400}
  .sp-lbl{color:var(--sub);font-size:12px;margin-top:4px}
  .sp-bar{height:6px;border-radius:3px;background:#1b2438;overflow:hidden;
    margin:10px 0 8px}
  .sp-bar > i{display:block;height:100%;width:30%;border-radius:3px;
    background:linear-gradient(90deg,var(--down),var(--up));
    animation:sp-slide 1.1s ease-in-out infinite}
  @keyframes sp-slide{0%{transform:translateX(-100%)}100%{transform:translateX(340%)}}
  .sp-detail{color:var(--sub);font-size:11.5px;line-height:1.7;margin-top:10px}
  .sp-err{color:var(--bad);font-size:11.5px;line-height:1.6;margin-top:8px}
  .sp-progress{color:var(--sub);font-size:12.5px;margin:0 0 2px}"""

TOOLBAR_ANCHOR = '    <button onclick="showNetinfo()">🌐 网络信息</button>'
TOOLBAR_NEW = TOOLBAR_ANCHOR + "\n" + '    <button onclick="showSpeedtest()">⚡ 一键测速</button>'

SPEED_MODAL = """<div id="speedModal" class="modal-overlay" onclick="if(event.target===this)closeSpeedtest()">
  <div class="modal">
    <div class="modal-head"><h3>⚡ 一键测网速</h3><div class="spacer"></div>
      <button id="spStartBtn" onclick="spStart()">开始测速</button>
      <button id="spCancelBtn" onclick="spCancel()" disabled>取消</button>
      <button onclick="closeSpeedtest()">关闭</button></div>
    <div class="modal-body">
      <div id="spProgress" class="sp-progress"></div>
      <div id="spBar" class="sp-bar" style="display:none"><i></i></div>
      <div id="spHero" class="sp-hero"></div>
      <div id="spDetail" class="sp-detail"></div>
      <div id="spErr" class="sp-err"></div>
      <div class="ni-note" style="margin-top:12px">数据源：Cloudflare 边缘节点（speed.cloudflare.com）。
        下载走明文 HTTP 直连，绕开本机代理与 TLS 阻断；
        上传页需 TLS，若被网络策略阻断则显示 “—”，不影响下载结果。</div>
    </div>
  </div>
</div>"""

SCRIPT_ANCHOR = "initCharts();\npoll();\nsetInterval(poll,1000);"

SPEED_JS = r"""function showSpeedtest(){
  document.getElementById("speedModal").style.display="flex";
  spRefresh();
}
function closeSpeedtest(){document.getElementById("speedModal").style.display="none";}

var _spTimer=null;
function spStopPoll(){ if(_spTimer){clearInterval(_spTimer); _spTimer=null;} }

function spMbps(n){ return (n===null||n===undefined) ? "—" : (n<10 ? n.toFixed(2) : n.toFixed(1)); }

function spRender(d){
  const running = d.phase==="running";
  const hero=document.getElementById("spHero");
  const bar=document.getElementById("spBar");
  const pr=document.getElementById("spProgress");
  const det=document.getElementById("spDetail");
  const err=document.getElementById("spErr");
  if(!hero) return;
  bar.style.display = running ? "block" : "none";
  pr.textContent = running ? (d.progress||"测试中…") : "";
  let h="";
  const rtt = (d.rtt_ms===null||d.rtt_ms===undefined) ? "—" : Math.round(d.rtt_ms);
  h += '<div class="sp-item"><div class="sp-num sp-rtt">' + rtt + '<span class="sp-unit">ms</span></div><div class="sp-lbl">延迟（TCP 握手）</div></div>';
  const dl = d.download_mbps;
  h += '<div class="sp-item"><div class="sp-num ' + (dl?"":"sp-idle") + '">' + spMbps(dl) + '<span class="sp-unit">Mbps</span></div><div class="sp-lbl">下载速度</div></div>';
  const up = d.upload_mbps;
  h += '<div class="sp-item"><div class="sp-num sp-up ' + (up?"":"sp-idle") + '">' + spMbps(up) + '<span class="sp-unit">Mbps</span></div><div class="sp-lbl">上传速度</div></div>';
  hero.innerHTML = h;
  const rows=[];
  if(d.down_bytes) rows.push("下载 " + (d.down_bytes/1048576).toFixed(2) + " MB / " + (d.down_seconds||0) + " s");
  if(d.up_bytes) rows.push("上传 " + (d.up_bytes/1048576).toFixed(2) + " MB / " + (d.up_seconds||0) + " s");
  if(d.up_note) rows.push(esc(d.up_note));
  if(d.started_at && d.finished_at) rows.push("本次耗时 " + (d.finished_at-d.started_at).toFixed(1) + " s");
  det.innerHTML = rows.map(x=>"<div>"+esc(x)+"</div>").join("");
  let em="";
  if(d.download_error && !d.download_mbps) em += "下载失败：" + esc(d.download_error);
  if(d.upload_error) em += (em?"<br>":"") + "上传失败：" + esc(d.upload_error);
  if(d.ping_error && (d.rtt_ms===null||d.rtt_ms===undefined)) em += (em?"<br>":"") + "延迟探测失败：" + esc(d.ping_error);
  if(d.error) em += (em?"<br>":"") + esc(d.error);
  err.innerHTML = em;
  const btn=document.getElementById("spStartBtn");
  if(btn){
    btn.textContent = running ? "测试中…" : "开始测速";
    btn.disabled = running;
    btn.style.opacity = running ? ".55" : "1";
  }
  const cbtn=document.getElementById("spCancelBtn");
  if(cbtn) cbtn.disabled = !running;
}

async function spFetch(q){
  try{
    const r=await fetch("/api/speedtest"+(q||""),{cache:"no-store"});
    if(!r.ok) throw new Error("HTTP "+r.status);
    return await r.json();
  }catch(e){ return {error:String(e.message||e)}; }
}

function spPoll(){
  spFetch("").then(d=>{
    if(d.error){ spStopPoll(); return; }
    spRender(d);
    if(d.phase!=="running") spStopPoll();
  });
}

function spRefresh(){
  spStopPoll();
  spFetch("").then(d=>{
    spRender(d.error?{phase:"error",error:d.error}:d);
    if(d.phase==="running") _spTimer=setInterval(spPoll,500);
  });
}

function spStart(){
  spStopPoll();
  spFetch("?start=1").then(d=>{
    spRender(d);
    if(d.phase==="running") _spTimer=setInterval(spPoll,500);
  });
}

function spCancel(){
  spFetch("?cancel=1").then(d=>{ spRender(d); spStopPoll(); });
}"""


def patch_speed(html: str) -> str:
    """把测速按钮 + 弹窗 + JS 注入 index.html。幂等。"""
    if "showSpeedtest" in html and 'id="speedModal"' in html:
        return html

    # 1) 样式
    anchor = "  .ni-kv .ni-val{font-size:15px}\n"
    if anchor in html:
        html = html.replace(anchor, anchor + SPEED_CSS, 1)
    else:
        assert "</style>" in html, "style anchor missing"
        html = html.replace("</style>", SPEED_CSS + "\n</style>", 1)

    # 2) 工具栏按钮
    assert TOOLBAR_ANCHOR in html, "toolbar button anchor not found"
    html = html.replace(TOOLBAR_ANCHOR, TOOLBAR_NEW, 1)

    # 3) 弹窗（放到 </body> 前）
    assert "</body>" in html, "body end missing"
    html = html.replace("</body>", "\n" + SPEED_MODAL + "\n</body>", 1)

    # 4) JS（放到 initCharts(); 之前，保证 esc() 已定义）
    assert SCRIPT_ANCHOR in html, "script tail anchor not found"
    html = html.replace(SCRIPT_ANCHOR, SPEED_JS + "\n" + SCRIPT_ANCHOR, 1)
    return html
