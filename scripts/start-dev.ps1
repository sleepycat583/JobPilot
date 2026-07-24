<#
一键启动本地开发用的前后端服务（正式后端，非 Playwright 验证用的假后端）。

分别在两个新的 PowerShell 窗口中启动：
  - 后端：.venv 里的 uvicorn app.api:app --reload，端口 8000
  - 前端：frontend 目录下的 npm run dev，端口 5173（默认代理到 8000）
关闭对应窗口即可停止该服务；两个服务互相独立，互不影响。

依赖前提：
  - 已执行过 python -m venv .venv 并 pip install -e ".[dev]"
  - 已在项目根目录准备好 .env（参考 .env.example）
  - 已执行过 cd frontend; npm install
#>

$ErrorActionPreference = 'Stop'

# scripts/ 的上一级就是项目根目录
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$envFile = Join-Path $repoRoot '.env'
$frontendDir = Join-Path $repoRoot 'frontend'

if (-not (Test-Path $venvPython)) {
    Write-Error ('未找到虚拟环境: ' + $venvPython + '。请先执行: python -m venv .venv 然后 pip install -e ".[dev]"')
}

if (-not (Test-Path $envFile)) {
    Write-Warning '未找到 .env，后端可能无法正常加载模型/数据库配置。请从 .env.example 复制一份并填写。'
}

if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
    Write-Warning '未找到 frontend/node_modules，请先执行: cd frontend; npm install'
}

Write-Host '启动后端 (uvicorn app.api:app --reload, http://127.0.0.1:8000) ...' -ForegroundColor Cyan
$backendCommand = 'Set-Location -LiteralPath ' + "'" + $repoRoot + "'" + '; & ' + "'" + $venvPython + "'" + ' -m uvicorn app.api:app --reload --port 8000'
Start-Process powershell -ArgumentList @('-NoExit', '-Command', $backendCommand)

Write-Host '启动前端 (npm run dev, http://127.0.0.1:5173) ...' -ForegroundColor Cyan
$frontendCommand = 'Set-Location -LiteralPath ' + "'" + $frontendDir + "'" + '; npm run dev'
Start-Process powershell -ArgumentList @('-NoExit', '-Command', $frontendCommand)

Write-Host '已启动。后端: http://127.0.0.1:8000  前端: http://127.0.0.1:5173' -ForegroundColor Green
Write-Host '关闭对应的新窗口即可停止该服务。' -ForegroundColor Green
