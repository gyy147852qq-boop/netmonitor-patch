# NetMonitor 增强工程

对 **网络流量监控（NetMonitor）** 这个 PyInstaller 单文件程序做功能增强的源码工程。

NetMonitor 的源码不在磁盘上 —— 程序发布时只有一个 28MB 的 `NetMonitor.exe`，
Python 代码以 code object 的形式压在里面。这个工程提供了一套工具链，
可以在**不重写程序**的前提下，把新功能安全地打进那个 exe。

---

## 目录结构

```
src/       补丁源码（会被编译成 code object 塞进 exe）
tools/     构建与解包工具链
docs/      设计与修复说明
.gitignore 排除 .work/ 解包产物与 dist/ 构建产物
```

---

## 功能清单

### ⚡ 一键测速 （本次新增）

在标题栏加一个测速按钮，测量三项指标：

| 指标 | 实现 |
|------|------|
| 延迟 | TCP 握手到 `speed.cloudflare.com:443` 的 RTT，3 次取最优 |
| 下载 | 明文 HTTP 分级拉取 `/__down`，1MB → 8MB → 32MB 自适应（跑到 3 秒即判定够准） |
| 上传 | HTTPS POST 4MB 到 `/__up` |

实测（真实网络）：

```
延迟 185.8 ms  |  下载 0.86 Mbps  |  上传 10.75 Mbps
```

```
GET /api/speedtest            # 当前状态
GET /api/speedtest?start=1    # 启动测速（异步）
GET /api/speedtest?cancel=1   # 取消
```

设计要点：下载走**明文 HTTP 并显式禁用系统代理**，因为目标机器 TLS 直连被重置
（`WinError 10054`）、代理对测速域名不可用。上传必须 TLS，被阻断时显示「—」
并标注「仅测下载」——**任何单项失败都不阻塞整体出结果**。

详见 [docs/speedtest.md](docs/speedtest.md)。

### 🌐 网络信息面板修复

原来的「出口 IP」其实是直连 IP，导致「出口 IP ≠ 直连 IP」这条最关键的代理判据永远失效。
根本原因是 CPython 的 `getproxies()`：只要环境里有 `NO_PROXY`，它就只返回
`{'no': ...}`，注册表里的系统代理完全读不到。

修复后：绕开 `getproxies()` 直接读注册表，出口/直连两路并发竞速，
超时从 8.5s 降到 0.7s，并加 15 秒缓存。参见 `src/netfix.py`。

### 📊 图表 x 轴时间刻度

detailChart 的 xAxis 是 `type:"time"`，但 `axisLabel:{show:false}` 把标签全关了，
横轴没有任何时间信息。修复后显示 `HH:MM:SS`，并调整 `grid.bottom` 让出高度。
参见 `src/ui_axis.py`。

### 🚦 下行限速（实验性，未合入）

基于 WinDivert 的分进程/全局下行限速引擎，见 `src/limiter.py` + `src/app_extra.py`。
**未安装到当前版本**：需要额外把 WinDivert 驱动和 `nb_limiter` 模块打进 PYZ，
而本项目当前只改 `app` 与 `web/index.html` 两个条目。保留作为参考实现。

---

## 技术约束（为什么补丁代码长这样）

这是整个工程最重要的部分。PyInstaller 单文件程序可修改的只有归档里 `app` 这个条目
（一个 Python code object 的 marshal）。所以只能按名字替换**已存在**的顶层 code object。

**三条硬约束：**

1. **只能替换已存在的顶层名字**
   模块字节码不会为新名字生成 `MAKE_FUNCTION`。新增的顶层函数塞进 `co_consts`
   也永远不会被实例化 —— 是死代码。辅助逻辑必须写成**嵌套函数**。

2. **`co_argcount` 必须与原函数一致**
   模块里那条 `MAKE_FUNCTION` 传入的默认值个数没变。给函数加参数会直接抛
   `TypeError: missing 1 required positional argument`。

3. **补丁文件顶层的 `import` / 常量赋值永不执行**
   只有函数 code object 被取走，模块级字节码被丢弃。所有 import 和常量
   **必须写进函数体**。

此外还有两个容易踩的坑：

- **递归替换的风险**：`build.py` 的 `walk()` 会递归遍历所有嵌套 code object
  并按名字匹配。如果嵌套辅助函数和顶层替换目标同名，会被互相替换成一团。
- **类体替换**：替换 `Handler` 类时，基类由**模块级**的 `LOAD_BUILD_CLASS`
  + `LOAD_NAME BaseHTTPRequestHandler` 传参，不受类体替换影响；但类体里仍需
  显式写 `class Handler(BaseHTTPRequestHandler)` 以保证继承链正确。

---

## 用法

### 1. 解包基线

从本机已安装的 NetMonitor 解出离线测试所需的参考树（`.work/`，已被 git 忽略）：

```powershell
py -3 tools/bootstrap.py
```

### 2. 构建

```powershell
py -3 tools/build.py            # 构建到 dist/，含自检
py -3 tools/build.py --install  # 构建并安装（自动备份）
```

### 3. 自检

```powershell
py -3 tools/verify.py    # 校验构建产物
py -3 tools/check_exe.py # 检查 exe 完整性
```

---

## 构建自检输出

```
entries   : 120 -> 120，顺序一致
changed   : [('app', 53104, 63842), ('web/index.html', 24711, 30458)]
bootloader: 一致
PYZ       : 一致（5059292 bytes）
```

**只有 `app` 和 `web/index.html` 两个条目变化**，引导段（bootloader）与 PYZ
完全未动 —— 风险面最小化，出问题可直接用 `.bak-时间戳` 备份回滚。

---

## 免责说明

本项目仅用于对**自己机器上已安装的软件**做功能增强与学习研究。
所有补丁都基于公开的 PyInstaller 归档格式（`MEI\x0c\x0b\x0a\x0b\x0e` cookie），
不包含任何破解、绕过授权或分发他人软件的内容。
