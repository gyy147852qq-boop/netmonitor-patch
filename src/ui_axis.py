# -*- coding: utf-8 -*-
"""
ui_axis.py —— 给「Top 5 进程下行速率 / 进程历史」这张图的 x 轴加上时间刻度。

原状态：detailChart 的 xAxis 是 type:"time"，但 axisLabel:{show:false} 把标签
整个关掉了，而且 grid.bottom 只有 22px，本来也放不下。页面上只能看到 y 轴的
B/s 数值，横轴完全没有时间信息，看不出这些速率对应哪一段时间。

改动：
  * 打开 x 轴标签，格式 HH:MM:SS
  * grid.bottom 22 -> 32，给标签让出高度
  * 标签 10px + hideOverlap，窄图时自动丢重叠标签而不是糊成一团
  * top5Panel 高度 34% -> 37%，避免加了标签后绘图区被压扁
"""

OLD_GRID = 'grid:{left:48,right:14,top:22,bottom:22},'
NEW_GRID = 'grid:{left:48,right:14,top:22,bottom:32},'

OLD_XAXIS = ('xAxis:{type:"time",axisLine:{lineStyle:{color:"#26314a"}},'
             'axisLabel:{show:false},splitLine:{show:false}},')
NEW_XAXIS = ('xAxis:{type:"time",axisLine:{lineStyle:{color:"#26314a"}},\n'
             '      axisLabel:{show:true,color:"#8ea0c0",fontSize:10,hideOverlap:true,\n'
             '        formatter:v=>{const d=new Date(v);return d.getHours().toString().padStart(2,"0")+":"\n'
             '          +d.getMinutes().toString().padStart(2,"0")+":"\n'
             '          +d.getSeconds().toString().padStart(2,"0");}},\n'
             '      splitLine:{show:false}},')

OLD_CSS = '#top5Panel{height:34%}'
NEW_CSS = '#top5Panel{height:37%}'


def patch_detail_axis(html: str) -> str:
    for old, new, what in ((OLD_GRID, NEW_GRID, "detailChart grid.bottom"),
                           (OLD_XAXIS, NEW_XAXIS, "detailChart xAxis.axisLabel"),
                           (OLD_CSS, NEW_CSS, "#top5Panel height")):
        n = html.count(old)
        assert n == 1, "expected exactly 1 occurrence of %s, found %d" % (what, n)
        html = html.replace(old, new, 1)
    return html