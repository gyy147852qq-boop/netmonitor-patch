# ============================================================
#  推送 NetMonitor 增强工程到 GitHub
#  仓库：https://github.com/gyy147852qq-boop/netmonitor-patch
#  用法：双击运行（或在终端执行）
# ============================================================
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  NetMonitor 增强工程 -> GitHub" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ---- 前置检查 ----
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "  [错误] 未找到 git" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

$remote = git remote get-url origin 2>$null
Write-Host "  远程地址 : $remote"
Write-Host "  当前分支 : $(git branch --show-current)"
Write-Host "  提交数量 : $((git rev-list --count HEAD))"
Write-Host ""

# ---- 检查仓库是否已在 GitHub 上创建 ----
$repoUrl = $remote -replace '\.git$', ''
Write-Host "  检查仓库是否存在..."
$exists = $false
try {
    $resp = Invoke-WebRequest -Uri $repoUrl -Method Head -TimeoutSec 15 -UseBasicParsing -ErrorAction Stop
    $exists = ($resp.StatusCode -eq 200)
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 404) { $exists = $false }
    else { $exists = $null }
}

if ($exists -eq $false) {
    Write-Host ""
    Write-Host "  [需要先创建仓库]" -ForegroundColor Yellow
    Write-Host "  请先在浏览器打开下面地址，点 New 创建空仓库（不要勾选 README/.gitignore）:" -ForegroundColor Yellow
    Write-Host "    https://github.com/new?name=netmonitor-patch" -ForegroundColor Cyan
    Write-Host ""
    Start-Process "https://github.com/new?name=netmonitor-patch&description=PyInstaller+patch+toolkit+for+NetMonitor+%28adds+one-click+speed+test%29"
    Write-Host "  创建完成后回到这里按回车继续..."
    Read-Host
} elseif ($exists -eq $null) {
    Write-Host "  [警告] 无法确认仓库状态，继续尝试推送" -ForegroundColor Yellow
} else {
    Write-Host "  仓库已存在" -ForegroundColor Green
}

Write-Host ""
Write-Host "  开始推送..." -ForegroundColor Cyan
Write-Host ""

git push -u origin main

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Green
    Write-Host "  推送成功！" -ForegroundColor Green
    Write-Host "============================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  $repoUrl" -ForegroundColor Cyan
    Write-Host ""
    Start-Process $repoUrl
} else {
    Write-Host ""
    Write-Host "  推送失败。常见原因：" -ForegroundColor Red
    Write-Host "    - 未登录 / token 过期  -> 会弹出登录窗口，登录后重试" -ForegroundColor Yellow
    Write-Host "    - 仓库名不对          -> 检查 https://github.com/gyy147852qq-boop" -ForegroundColor Yellow
    Write-Host "    - 远程已有内容        -> 需要先 pull --rebase" -ForegroundColor Yellow
}

Write-Host ""
Read-Host "按回车退出"
