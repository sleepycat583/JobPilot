[CmdletBinding()]
param(
    [ValidateSet('start', 'check', 'stop')]
    [string]$Action = 'start',
    [ValidateRange(1024, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 5173,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$RuntimeDir = Join-Path $RepoRoot 'output\local'
$BackendEnv = Join-Path $BackendDir '.env'
$BackendEnvExample = Join-Path $BackendDir '.env.example'
$BackendPidFile = Join-Path $RuntimeDir 'backend.pid'
$FrontendPidFile = Join-Path $RuntimeDir 'frontend.pid'
$BackendLog = Join-Path $RuntimeDir 'backend.log'
$BackendErrorLog = Join-Path $RuntimeDir 'backend.err'
$FrontendLog = Join-Path $RuntimeDir 'frontend.log'
$FrontendErrorLog = Join-Path $RuntimeDir 'frontend.err'

Set-Location $RepoRoot

function Write-Info([string]$Message) {
    Write-Host "[local] $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[local] $Message" -ForegroundColor Green
}

function Fail([string]$Message) {
    throw "[local] $Message"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $false)][string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    Push-Location $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            Fail "命令执行失败（退出码 $LASTEXITCODE）：$FilePath $($ArgumentList -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Get-CommandPath([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return $null
    }
    $path = $command.Path
    if ($path -and [IO.Path]::GetExtension($path) -eq '.ps1') {
        $commandDirectory = Split-Path -Parent $path
        $commandBaseName = [IO.Path]::GetFileNameWithoutExtension($path)
        foreach ($extension in @('.cmd', '.exe')) {
            $nativePath = Join-Path $commandDirectory ($commandBaseName + $extension)
            if (Test-Path $nativePath) {
                return $nativePath
            }
        }
    }
    return $path
}

function Get-VersionTuple([string]$FilePath, [string[]]$Arguments) {
    $output = (& $FilePath @Arguments 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Fail "无法读取依赖版本：$FilePath"
    }
    $match = [regex]::Match($output, '(?<major>\d+)\.(?<minor>\d+)(?:\.(?<patch>\d+))?')
    if (-not $match.Success) {
        Fail "无法解析依赖版本：$FilePath"
    }
    return @(
        [int]$match.Groups['major'].Value,
        [int]$match.Groups['minor'].Value,
        $(if ($match.Groups['patch'].Success) { [int]$match.Groups['patch'].Value } else { 0 })
    )
}

function Assert-Dependencies {
    $python = Get-CommandPath 'python'
    $uv = Get-CommandPath 'uv'
    $node = Get-CommandPath 'node'
    $npm = Get-CommandPath 'npm'
    if (-not $python) { Fail '未找到 Python。请安装 Python 3.11 或 3.12，并重新打开 PowerShell。' }
    if (-not $uv) { Fail '未找到 uv。请按 https://docs.astral.sh/uv/getting-started/installation/ 安装 uv。' }
    if (-not $node) { Fail '未找到 Node.js。请安装 Node.js 20 或更高版本，并重新打开 PowerShell。' }
    if (-not $npm) { Fail '未找到 npm。请确认 Node.js 安装包含 npm。' }

    $pythonVersion = Get-VersionTuple $python @('--version')
    if ($pythonVersion[0] -ne 3 -or $pythonVersion[1] -lt 11 -or $pythonVersion[1] -ge 13) {
        Fail "Python 版本不受支持：$($pythonVersion -join '.')。项目要求 Python 3.11 或 3.12。"
    }
    $nodeVersion = Get-VersionTuple $node @('--version')
    if ($nodeVersion[0] -lt 20) {
        Fail "Node.js 版本过低：$($nodeVersion -join '.')。项目要求 Node.js 20 或更高版本。"
    }
    $null = Get-VersionTuple $npm @('--version')
    $null = Get-VersionTuple $uv @('--version')
    return @{
        Python = $python
        Uv = $uv
        Node = $node
        Npm = $npm
    }
}

function Ensure-LocalEnv {
    if (Test-Path $BackendEnv) {
        Write-Info '检测到 backend/.env，保持现有配置不变。'
        return
    }
    if (-not (Test-Path $BackendEnvExample)) {
        Fail '缺少 backend/.env.example，无法创建本地配置。'
    }
    Copy-Item -LiteralPath $BackendEnvExample -Destination $BackendEnv
    Write-Ok '已从 backend/.env.example 创建 backend/.env；当前默认为 Stub 模式。'
}

function Get-RecordedPid([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $raw = (Get-Content -LiteralPath $Path -Raw).Trim()
    $pid = 0
    if (-not [int]::TryParse($raw, [ref]$pid) -or $pid -le 0) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $pid
}

function Get-ManagedProcess([string]$PidFile) {
    $pid = Get-RecordedPid $PidFile
    if ($null -eq $pid) { return $null }
    return Get-Process -Id $pid -ErrorAction SilentlyContinue
}

function Get-PortOwner([int]$Port) {
    try {
        return @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    }
    catch {
        return @()
    }
}

function Assert-PortAvailable([int]$Port, [string]$ServiceName) {
    $owners = Get-PortOwner $Port
    if ($owners.Count -eq 0) { return }
    $pids = ($owners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
    Fail "$ServiceName 端口 $Port 已被占用（PID: $pids）。请使用其他端口参数，或先手动停止占用进程；脚本不会结束无关进程。"
}

function Save-Pid([string]$Path, [int]$Pid) {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    Set-Content -LiteralPath $Path -Value ([string]$Pid) -Encoding ascii
}

function Stop-ProcessTree([int]$Pid) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $Pid" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        Stop-ProcessTree ([int]$child.ProcessId)
    }
    $process = Get-Process -Id $Pid -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        Stop-Process -Id $Pid -Force -ErrorAction SilentlyContinue
        Write-Info "已停止受脚本管理的进程 PID $Pid。"
    }
}

function Stop-ManagedService([string]$Name, [string]$PidFile) {
    $pid = Get-RecordedPid $PidFile
    if ($null -eq $pid) {
        Write-Info "$Name 没有脚本记录的运行进程。"
        return
    }
    Stop-ProcessTree $pid
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Test-Url([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

function Wait-Url([string]$Url, [string]$Label, [int]$TimeoutSeconds = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $Url) {
            Write-Ok "$Label 已就绪。"
            return
        }
        Start-Sleep -Seconds 1
    }
    Fail "$Label 在 $TimeoutSeconds 秒内未就绪，请查看 output/local 下的日志。"
}

function Invoke-HealthCheck {
    $checks = @(
        @{ Label = '后端存活检查'; Url = "http://127.0.0.1:$BackendPort/api/health/live" },
        @{ Label = '后端就绪检查'; Url = "http://127.0.0.1:$BackendPort/api/health/ready" },
        @{ Label = '前端页面检查'; Url = "http://127.0.0.1:$FrontendPort/" }
    )
    $failed = $false
    foreach ($check in $checks) {
        if (Test-Url $check.Url) {
            Write-Ok "$($check.Label)：通过"
        }
        else {
            Write-Host "[local] $($check.Label)：失败 ($($check.Url))" -ForegroundColor Red
            $failed = $true
        }
    }
    if ($failed) { Fail '本地服务健康检查未全部通过。' }
}

function Start-LocalServices {
    $tools = Assert-Dependencies
    Ensure-LocalEnv
    if ($null -ne (Get-ManagedProcess $BackendPidFile) -or $null -ne (Get-ManagedProcess $FrontendPidFile)) {
        Fail '检测到脚本已经启动的服务。请先执行 -Action stop，或使用 -Action check 查看状态。'
    }
    Assert-PortAvailable $BackendPort '后端'
    Assert-PortAvailable $FrontendPort '前端'
    if (-not $SkipInstall) {
        Write-Info '正在按 uv.lock 同步 Python 依赖。'
        Invoke-Checked $tools.Uv @('sync', '--dev') $BackendDir
        Write-Info '正在按 package-lock.json 安装前端依赖。'
        Invoke-Checked $tools.Npm @('ci') $FrontendDir
    }
    Write-Info '正在执行数据库迁移。'
    Invoke-Checked $tools.Uv @('run', 'alembic', 'upgrade', 'head') $BackendDir

    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    $backendOrigin = "http://127.0.0.1:$FrontendPort"
    $frontendProxy = "http://127.0.0.1:$BackendPort"
    $oldOrigin = $env:FRONTEND_ORIGIN
    $oldProxy = $env:API_PROXY_TARGET
    try {
        $env:FRONTEND_ORIGIN = $backendOrigin
        $backend = Start-Process -FilePath $tools.Uv -WorkingDirectory $BackendDir -ArgumentList @('run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$BackendPort) -RedirectStandardOutput $BackendLog -RedirectStandardError $BackendErrorLog -PassThru
        Save-Pid $BackendPidFile $backend.Id
        $env:API_PROXY_TARGET = $frontendProxy
        $frontend = Start-Process -FilePath $tools.Npm -WorkingDirectory $FrontendDir -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', [string]$FrontendPort) -RedirectStandardOutput $FrontendLog -RedirectStandardError $FrontendErrorLog -PassThru
        Save-Pid $FrontendPidFile $frontend.Id
    }
    finally {
        $env:FRONTEND_ORIGIN = $oldOrigin
        $env:API_PROXY_TARGET = $oldProxy
    }
    try {
        Wait-Url "http://127.0.0.1:$BackendPort/api/health/live" '后端存活检查'
        Wait-Url "http://127.0.0.1:$BackendPort/api/health/ready" '后端就绪检查'
        Wait-Url "http://127.0.0.1:$FrontendPort/" '前端页面'
    }
    catch {
        Write-Host "[local] 启动失败，正在停止本次启动的服务。" -ForegroundColor Yellow
        Stop-ManagedService '后端' $BackendPidFile
        Stop-ManagedService '前端' $FrontendPidFile
        throw
    }
    Write-Ok "本地工作台已启动： http://127.0.0.1:$FrontendPort/"
    Write-Info "OpenAPI： http://127.0.0.1:$BackendPort/docs"
    Write-Info "日志： $RuntimeDir"
}

try {
    switch ($Action) {
        'start' { Start-LocalServices }
        'check' { Invoke-HealthCheck }
        'stop' {
            Stop-ManagedService '后端' $BackendPidFile
            Stop-ManagedService '前端' $FrontendPidFile
            Write-Ok '本地服务已停止；backend/data 未被删除。'
        }
    }
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
