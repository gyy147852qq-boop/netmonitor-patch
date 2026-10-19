# 修复：最小化几分钟后程序自动关停

- 日期：2026-10-18
- 影响版本：NetMonitor 2026.10.17（release/NetMonitor-2026.10.17.exe）
- 修复后：`C:\Users\Administrator\AppData\Local\NetMonitor\NetMonitor.exe`
- 改动文件：`src/netfix.py`（新增 `_watchdog` 替换）、`tools/build.py`（幂等化）

## 现象

主窗口最小化后放几分钟，整个程序（含后台采集线程）无声无息地消失。
`%TEMP%\QMNetMonitor\startup.log` 里只有 `boot ...` 记录，`error.log` 里没有任何异常，
说明不是崩溃，而是被**主动**结束的。

## 根因

`app` 模块里的看门狗线程：

```python
def _watchdog():
    while True:
        time.sleep(3)
        if time.time() - _state['last_poll'] > 15:
            _shutdown()      # -> os._exit(0)
            return
```

`last_poll` 由前端（Edge `--app` 窗口里的 index.html）每 1 秒请求 `/api/stats` 时刷新。
窗口一旦最小化，Chromium 就会对隐藏页面做定时器节流（隐藏几分钟后降到 1 次/分钟，
甚至直接冻结页面），轮询间隔必然超过 15 秒 —— 看门狗便认定"用户已经关了窗口"，
调 `os._exit(0)` 把进程干掉。所以现象是"最小化几分钟后自动关停"，
且**不产生任何错误日志**。

## 修法

看门狗的判据从"前端还在不在轮询"改成"我们启动的那个 Edge 窗口进程还在不在"：

```python
def _watchdog():
    def edge_alive():   # 枚举 msedge.exe，命令行里带 edge-profile 即视为窗口还在
        ...
    seen = False; miss = 0
    while True:
        time.sleep(5)
        alive = edge_alive()
        if alive:            # 最小化 / 隐藏都算活着 -> 绝不退出
            seen = True; miss = 0; continue
        if alive is False:
            if seen:         # 见过窗口，现在没了 -> 连续 2 次确认后退出
                miss += 1
                if miss >= 2: _shutdown(); return
            elif time.time() - max(t0, _state.get("last_poll") or 0) > 600:
                _shutdown(); return   # 回退到默认浏览器打开时的兜底
```

要点：

- 最小化 / 页面被冻结不再影响判断，窗口进程还在就一直跑；
- 用户真正关掉窗口 → Edge 进程退出 → 约 10 秒内程序正常退出（保留原行为）；
- 找不到 Edge、回退到系统默认浏览器时，退回轮询超时判断，但阈值从 15s 放宽到 10 分钟，
  并从看门狗启动时刻起算，避免启动瞬间被误判；
- `psutil` 在 app 里是 `try: import psutil except: psutil = None` 的，取不到时
  `edge_alive()` 返回 None，看门狗这一轮不做判断（宁可留着也不误杀）。

## 验证

`tools/` 下的流水线构建，产物 `dist/NetMonitor-fixed.exe`，自检通过（仅 `app` 条目变化，
120 条目顺序一致、引导段与 PYZ 未变）。

行为测试（加载产物里真实的 `app` 字节码执行）：

| 场景 | 旧版 | 新版 |
| --- | --- | --- |
| 窗口在、前端完全不轮询 25s（等价最小化/冻结） | 15s 内退出 ❌ | 不退出 ✅ |
| 关掉 Edge 窗口 | — | 约 10s 内退出 ✅ |

另用真实 Edge（`--app` + 独立 `edge-profile`）验证进程识别：窗口打开时检出 True，
关闭后检出 False。

## 复现构建

```bash
py -3.13 tools/build.py            # 构建到 dist/
py -3.13 tools/build.py --install  # 自检通过后覆盖安装（自动备份到 NetMonitor.exe.bak-<时间戳>）
```

`tools/build.py` 现在对前端补丁做了幂等处理：基座已经打过 `ui.py` / `ui_axis.py` 补丁时
自动跳过，因此可以在"已安装版本"上反复叠加后端修复。
