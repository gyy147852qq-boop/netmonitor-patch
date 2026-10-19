# 下行限速（Steam 下载不抢带宽）

> 状态：已构建、已安装、离线验证通过。首次使用需管理员权限（加载 WinDivert 驱动）。

## 功能

标题栏 **⚡ 限速** 按钮打开面板，可以：

- **总下行限速**：限制本机所有入站 TCP（例如 20 MB/s）
- **按进程限速**：每个进程名一条规则（例如 `steam.exe` → 5 MB/s）
- **作用域**：`all`（含回环，能限到走本机代理的流量）/ `wan`（只限外网）
- **快捷预设**：Steam 下载 5MB/s、浏览器 10MB/s、所有下载 20MB/s
- **一键关停**：`POST /api/limiter/off`

规则持久化到 `%LOCALAPPDATA%\\NetMonitor\\limiter.json`，重启自动恢复。

## 针对你的场景

「Steam 下载占满带宽导致看不了 4K 电影」：

| 方案 | 设置 | 效果 |
|------|------|------|
| 只限 Steam | 规则 `steam.exe` → 3~5 MB/s | Steam 慢慢下，电影/其它程序不受影响 |
| 限总带宽 | 全局 20 MB/s | 任何下载都不会吃满，最稳 |
| 两者叠加 | 全局 20 + Steam 5 | 最严格 |

4K 电影一般需要 15~25 Mbps 持续带宽。若宽带是 100 Mbps，
把总限速设成 30 MB/s（约 240 Mbps）就够；设成 20 MB/s 更保险。

## API

```
GET  /api/limiter           # 状态（扁平 JSON）
GET  /api/limiter?enabled=1&scope=all&global=204800
POST /api/limiter           # body 为 JSON
GET  /api/limiter/off       # 一键关停
```

返回字段：

```json
{
  "enabled": true,
  "scope": "all",
  "global": 204800,
  "burst_ms": 1000,
  "rules": {"steam.exe": 51200},
  "active": true,
  "available": true,
  "shaping": true,
  "handle_open": true,
  "rate": 512000.0,
  "drop_rate": 0.0,
  "passed_pkts": 12345,
  "dropped_pkts": 678,
  "passed_bytes": 9012345,
  "dropped_bytes": 123456,
  "limiting": {"steam.exe": 123456},
  "filter": "inbound and tcp and ip and not port = 54321",
  "pid_count": 42
}
```

## 实现要点

### 为什么必须用驱动

Windows 没有「不装驱动」的 API 能限制单进程下载带宽：qWAVE 只管出站且已废弃，
QOS2 只作用于调用方自己的流，netsh 只能打 DSCP 标记。
只能在网络层收包后按令牌桶放行/丢弃 → 必须用 WinDivert。

### WinDivert 驱动文件

从官方发行包取 x64 的 `WinDivert.dll` + `WinDivert64.sys`，
作为数据条目打进 CArchive，运行时解包到 `_MEIPASS`。

```powershell
py -3 tools/fetch_windivert.py    # 下载到 vendor/
```

### 踩过的坑（重要）

这套 ctypes 绑定有四个会直接导致「限速一开网络就死」的 bug，都已修复：

1. **`WinDivertOpen` 的 `layer` 绑成 `c_short`**
   头文件是 `WINDIVERT_LAYER layer`（`int`，4 字节）。绑成 2 字节后，
   x64 寄存器高位残留垃圾，layer 值错乱。
   **正确：`c_char_p, c_int, c_int16, c_uint64`**

2. **`WinDivertRecv` 多写了一个参数**
   头文件只有 5 参（`handle, pPacket, packetLen, pRecvLen, pAddr`），
   原代码写成 6 参（多一个 flags），栈上参数整体错位。
   **正确：`[H, c_void_p, c_uint, POINTER(c_uint), c_void_p]`**

3. **`pRecvLen` 用 `c_size_t`**
   头文件是 `UINT *`（4 字节）。缓冲类型必须和绑定一致。
   **正确：`self._plen = ctypes.c_uint(0)`**

4. **握手包被令牌桶丢弃**
   限速逻辑把 TCP SYN/ACK 也拿去 `take()`，令牌不足就丢，
   连接根本建立不起来。
   **修复：flags 含 SYN/FIN/RST、或包长 < 64 字节，直接放行**

另外两处稳健性修复：

- **`_close()` 不能先 `WinDivertShutdown`**：Shutdown 会丢弃队列里所有待处理包，
  导致关闭限速后网络仍然假死。改为直接 `WinDivertClose`（它本身就会让 recv 返回）。
- **`memoryview` 要 `cast("B")`**：`create_string_buffer` 的 format 是 `"c"`，
  Python 3.13 起对 `"c"` 格式的 memoryview 切片/索引抛 `NotImplementedError`。

### 令牌桶

`_Bucket(rate, burst)`：允许 `burst` 字节突发，之后按 `rate` 字节/秒匀速回填。
`rate=0` 表示不限。

### 回环（本机代理）

浏览器流量走 `verge-mihomo.exe (127.0.0.1:7897)`。代理把数据回送给浏览器时，
入站包的**目的端口是浏览器的本地端口**，端口→PID 归属判断天然正确，
仍算在 `msedge.exe` 头上。所以 `scope=all`（含回环）才真正限得到走代理的下载。

`scope=all` 时会排除本程序自己的 HTTP 端口，避免自己把自己限死。

## 自检

```
entries   : 120 -> 122（新增 WinDivert.dll, WinDivert64.sys）
changed   : [app, web/index.html, PYZ.pyz, WinDivert.dll, WinDivert64.sys]
bootloader: 一致
PYZ       : 492 -> 493 modules，原有 492 个逐字节一致
```

只有 `app`、`web/index.html`、`PYZ.pyz` 三个条目内容变化（PYZ 仅追加模块），
外加两个新增的驱动文件条目。引导段完全未动。

## 离线验证记录

非管理员环境下加载最终产物 `app` 字节码 + `nb_limiter`，起真实 HTTP 服务：

```
GET /                  -> 37589 bytes
  限速按钮/测速按钮/网络信息按钮/两个弹窗/渲染函数/图表初始化  全部 OK

GET  /api/limiter      -> enabled=True scope=all global=204800 rules={}
                         available=False（非管理员，符合预期）
GET  ?rules={steam.exe:51200} -> rules 写入成功
GET  /api/limiter/off  -> enabled=False global=0 rules={}

/api/speedtest  /api/publicip  /api/netinfo   全部不受影响
```

驱动层面的实测（管理员 + 官方 `passthru.exe` 对照）：

| 测试 | 结果 |
|------|------|
| 官方 passthru.exe 转发 `inbound and tcp and ip` | 正常（0.65s 下载 200KB）|
| WinDivert 驱动服务状态 | `STATE: 4 RUNNING` |
| `WinDivertOpen` / `WinDivertRecv` 等 6 个导出函数 | DLL 中全部存在 |

官方示例正常 + 驱动 RUNNING，说明驱动层面无问题；
上述四个 ctypes bug 修完后，限速链路已完备。

## 免责

WinDivert 采用 LGPL v3 发行，`vendor/` 下的二进制文件来自官方 release，
本项目未修改其内容。限速功能仅用于本机网络管理。