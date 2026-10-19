# ============================================================
#  NetMonitor "一键测速" 补丁安装器
#  用法：双击本文件即可（会自动请求管理员权限）
# ============================================================
$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "安装 NetMonitor 一键测速补丁"

# ---- 自动提权 ----
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host ""
    Write-Host "  需要管理员权限才能替换程序文件，正在提权..." -ForegroundColor Yellow
    Write-Host ""
    try {
        Start-Process powershell -Verb RunAs -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
        )
    } catch {
        Write-Host "提权失败：$($_.Exception.Message)" -ForegroundColor Red
        Read-Host "按回车退出"
    }
    exit
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "   NetMonitor 一键测速 补丁安装" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ---- 定位文件 ----
$src = Join-Path $PSScriptRoot "NetMonitor-fixed.exe"
$dstDir = Join-Path $env:LOCALAPPDATA "NetMonitor"
$dst = Join-Path $dstDir "NetMonitor.exe"

if (-not (Test-Path $src)) {
    Write-Host "  [错误] 找不到补丁文件：" -ForegroundColor Red
    Write-Host "         $src" -ForegroundColor Red
    Write-Host "         请把本脚本和 NetMonitor-fixed.exe 放在同一个文件夹里。" -ForegroundColor Yellow
    Read-Host "按回车退出"; exit 1
}
if (-not (Test-Path $dst)) {
    Write-Host "  [错误] 找不到已安装的 NetMonitor：" -ForegroundColor Red
    Write-Host "         $dst" -ForegroundColor Red
    Write-Host "         请先确认程序已正常安装过。" -ForegroundColor Yellow
    Read-Host "按回车退出"; exit 1
}

Write-Host "  补丁文件 : $src"
Write-Host "  安装目标 : $dst"
Write-Host ""

# ---- 关闭运行中的实例 ----
$procs = Get-Process -Name NetMonitor -ErrorAction SilentlyContinue
if ($procs) {
    Write-Host "  检测到程序正在运行，正在关闭..." -ForegroundColor Yellow
    $procs | Stop-Process -Force
    Start-Sleep -Milliseconds 1200
    Write-Host "  已关闭" -ForegroundColor Green
}

# ---- 备份 ----
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bak = "$dst.bak-$stamp"
Copy-Item $dst $bak -Force
Write-Host "  已备份原文件 -> $(Split-Path $bak -Leaf)" -ForegroundColor Green

# ---- 安装 ----
Copy-Item $src $dst -Force
$newItem = Get-Item $dst
$newHash = (Get-FileHash $dst -Algorithm SHA256).Hash
$srcHash = (Get-FileHash $src -Algorithm SHA256).Hash

Write-Host "  已写入新文件 ($($newItem.Length) 字节)" -ForegroundColor Green

if ($newHash -ne $srcHash) {
    Write-Host ""
    Write-Host "  [警告] 文件校验不一致，安装可能失败！" -ForegroundColor Red
    Write-Host "         请用备份恢复：$bak" -ForegroundColor Yellow
    Read-Host "按回车退出"; exit 1
}
Write-Host "  文件校验通过 (SHA256)" -ForegroundColor Green

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  现在重新打开桌面上的「网络流量监控」快捷方式，" -ForegroundColor White
Write-Host "  标题栏右侧会多出一个 " -ForegroundColor White -NoNewline
Write-Host "⚡ 一键测速" -ForegroundColor Yellow -NoNewline
Write-Host " 按钮。" -ForegroundColor White
Write-Host ""
Write-Host "  点开后按「开始测速」即可测量 延迟 / 下载 / 上传。" -ForegroundColor Gray
Write-Host ""
Write-Host "  如需回滚，用备份覆盖回去即可：" -ForegroundColor Gray
Write-Host "    $bak" -ForegroundColor DarkGray
Write-Host ""
Read-Host "按回车退出"
