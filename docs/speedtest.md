# 一键测网速 — 功能说明

> 状态：补丁已构建并通过端到端验证，**待安装**（见文末「安装」）。

## 新增功能

在 NetMonitor 标题栏加一个 **⚡ 一键测速** 按钮，点开即测三项指标：

| 指标 | 实现 |
|------|------|
| 延迟 | TCP 握手到 `speed.cloudflare.com:443` 的 RTT，3 次取最优 |
| 下载 | 明文 HTTP 分级拉取 `speed.cloudflare.com/__down`，1MB → 8MB → 32MB 自适应（跑到 3 秒即判定够准） |
| 上传 | HTTPS POST 4MB 到 `speed.cloudflare.com/__up`（3 秒内完成） |

界面：进度条 + 三个阶段提示（延迟 → 下载 → 上传），可中途取消。

## 为什么下载走明文 HTTP

本机实测网络环境：

- TLS 直连被重置（`[WinError 10054]`）
- 系统代理 `127.0.0.1:7897` 对测速域名不可用
- **明文 HTTP 直连稳定**

因此下载**显式禁用系统代理**（`ProxyHandler({})`）并走明文 HTTP。
上传必须 TLS；若被网络策略阻断，界面显示「—」并标注「TLS 上传通道不通，仅测下载」，
**不会卡死，也不影响下载结果**。

这是一个优雅降级设计：任何单项失败都不阻塞整体出结果。

## 改动的文件

新增：

| 文件 | 作用 |
|------|------|
| `src/app_speed.py` | 后端：替换 `fetch_public_ip`（测速引擎）+ `Handler`（新增 `/api/speedtest`） |
| `src/ui_speed.py` | 前端：工具栏按钮 + 测速弹窗 + JS |

修改：

| 文件 | 改动 |
|------|------|
| `tools/build.py` | 接入上述两个补丁 |

产物：

| 文件 | 说明 |
|------|------|
| `dist/NetMonitor-fixed.exe` | 打好补丁的程序（28,653,304 字节） |
| `dist/install-speedtest-patch.ps1` | 一键安装脚本（自动提权 + 自动备份 + SHA256 校验） |

## 技术约束：为什么代码长这样

NetMonitor 是 PyInstaller 单文件程序，源码不在磁盘上。可修改的只有归档里的 `app` 条目
（一个 Python code object 的 marshal）。所以只能按名字替换**已存在**的顶层 code object。

三条硬约束：

1. **只能替换已存在的顶层名字** —— 模块字节码不会为新名字生成 `MAKE_FUNCTION`，
   新增的顶层函数塞进 `co_consts` 也永远不会被实例化（是死代码）。
2. **`co_argcount` 必须与原函数一致** —— 模块里那条 `MAKE_FUNCTION` 传入的默认值个数没变，
   给函数加参数会直接抛 `TypeError: missing 1 required positional argument`。
3. **补丁文件顶层的 `import` / 常量赋值永不执行** —— 只有函数 code object 被取走，
   模块级字节码被丢弃。所以所有 import 和常量必须写进函数体。

对应的做法：

- 测速引擎全部写成 `fetch_public_ip` 内的**嵌套函数**；
- 命令（start / cancel / status）用**线程局部变量** `_SPD_TLS` 传递，避免并发请求串味；
- `fetch_public_ip` 在没有测速命令时**保持原公网 IP 探测语义**，`/api/publicip` 行为完全不变；
- `Handler` 是类体，方法由类体自己的字节码创建，所以可以新增 `do_GET` 里的路由，
  但仍需显式写 `class Handler(BaseHTTPRequestHandler)` 保证继承链。

## API

```
GET /api/speedtest            # 当前状态
GET /api/speedtest?start=1    # 启动一轮测速（异步，立即返回）
GET /api/speedtest?cancel=1   # 取消进行中的测速
```

返回字段：

```json
{
  "phase": "idle | running | done | error",
  "progress": "正在测试下载速度…",
  "rtt_ms": 185.8,
  "download_mbps": 0.86,
  "upload_mbps": 10.75,
  "upload_available": true,
  "up_note": "",
  "down_bytes": 1000000,
  "down_seconds": 9.35,
  "up_bytes": 4000000,
  "up_seconds": 2.98,
  "download_error": null,
  "upload_error": null,
  "ping_error": null,
  "started_at": 1792421403.44,
  "finished_at": 1792421416.54
}
```

## 构建

```powershell
py -3 tools/build.py            # 构建到 dist/，含自检
py -3 tools/build.py --install  # 构建并安装（自动备份）
```

自检输出：

```
entries   : 120 -> 120，顺序一致
changed   : [('app', 53104, 63842), ('web/index.html', 24711, 30458)]
bootloader: 一致
PYZ       : 一致（5059292 bytes）
```

**只有 `app` 和 `web/index.html` 两个条目变化**，引导段（bootloader）与 PYZ 完全未动，
风险面最小化。

## 验证记录

### 1. 静态自检（对最终 exe）

- 120 个条目名称与顺序完全一致
- 仅 `app` + `web/index.html` 的 SHA256 变化
- bootloader 字节段一致、PYZ 一致
- `app` 顶层名字集合不变，仅 `fetch_public_ip` / `Handler` 的字节码变化，`argc` 均保持 0

### 2. 端到端运行验证（直接加载最终产物）

从 `dist/NetMonitor-fixed.exe` 取出 `app` 与 `web/index.html`，起真实 HTTP 服务跑真实网络：

```
GET /                  -> 200, 29068 bytes
  测速按钮/弹窗/开始按钮/结果区/渲染函数/API 路径  全部 OK
  原网络信息按钮、原图表初始化                      保留 OK

GET /api/publicip      -> {"ip": "13.250.172.188", "source": "...", "raw": "..."}
                          （原语义未变）

GET /api/speedtest     -> phase=idle
GET /api/speedtest?start=1 -> phase=running
[ 1.0s] running  rtt=185.8
[10.1s] running  dl=0.86
[13.1s] done     dl=0.86  up=10.75
```

## 安装

**双击 `dist/install-speedtest-patch.ps1`** 即可。脚本会：

1. 自动请求管理员权限
2. 关闭正在运行的 NetMonitor
3. 备份原文件为 `NetMonitor.exe.bak-时间戳`
4. 复制新文件并做 SHA256 校验
5. 提示"重新打开快捷方式"

装完重新打开桌面上的「网络流量监控」快捷方式，标题栏右侧就有 **⚡ 一键测速**。

### 回滚

把 `%LOCALAPPDATA%\NetMonitor\NetMonitor.exe.bak-时间戳` 改回 `NetMonitor.exe` 即可。

## 交付说明

本补丁已构建并通过验证，但**安装步骤需要在宿主机上执行**：

当前开发沙箱只允许写入 `C:\Users\Administrator\Documents`（工作区），
`%LOCALAPPDATA%\NetMonitor` 在工作区外，写入被沙箱拒绝。
因此安装留给用户双击脚本完成 —— 这也让用户保有一次确认机会。