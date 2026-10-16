# start-toonflow.ps1 - 启动 Toonflow v2.0.5（v3 合同：经 scripts/toonflow-loopback.ts 原生 createApp 薄包装，显式 127.0.0.1）
# 规则：绑定本机回环；窗口 Hidden；先查端口冲突；只管理本项目 PID（三重身份校验：exe 路径+entry 命令行+创建时间）；数据/缓存/日志全部在目标目录内。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

# 身份三重校验：exe 路径 + 命令行包含 entry + 进程创建时间一致（±2s）。任一不符=不是本任务进程（防 PID 复用误停/误认）。
function Confirm-OwnProcess($info, $proc) {
    # R1 加固（repair1）：登记 entry/exe/creation_time 必填、非空、可解析，且必须等于本项目规范 Bun（$Bun）/wrapper（$Entry）路径；
    # 进程实际 exe/命令行/创建时间元数据必须可得；任一缺失/不可解析/不匹配=拒绝复用（fail closed，防 PID 复用误认，拒绝后走全新启动）。
    if (-not $info -or -not $proc) { return $false }
    # pid 单独做正整数校验（登记的 pid=$proc.Id 是 int，不能混入字符串字段循环——协调方 2026-10-11 实证反馈）
    if (-not $info.PSObject.Properties["pid"] -or -not ($info.pid -is [int] -or $info.pid -is [long]) -or $info.pid -le 0) { return $false }
    foreach ($p in @("exe", "entry", "creation_time")) {
        $prop = $info.PSObject.Properties[$p]
        if (-not $prop -or -not ($prop.Value -is [string]) -or [string]::IsNullOrWhiteSpace($prop.Value)) { return $false }
    }
    try {
        [void][DateTime]::Parse($info.creation_time, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
    } catch { return $false }
    $canonExe = try { (Resolve-Path -LiteralPath $Bun -ErrorAction Stop).Path } catch { return $false }
    $canonEntry = try { (Resolve-Path -LiteralPath $Entry -ErrorAction Stop).Path } catch { return $false }
    $recExe = try { (Resolve-Path -LiteralPath $info.exe -ErrorAction Stop).Path } catch { return $false }
    $recEntry = try { (Resolve-Path -LiteralPath $info.entry -ErrorAction Stop).Path } catch { return $false }
    if ($recExe -ne $canonExe -or $recEntry -ne $canonEntry) { return $false }
    if (-not $proc.Path) { return $false }
    $procPath = try { (Resolve-Path -LiteralPath $proc.Path -ErrorAction Stop).Path } catch { $proc.Path }
    if ($procPath -ne $recExe) { return $false }
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.Id)" -ErrorAction SilentlyContinue
    if (-not $cim -or -not $cim.CommandLine -or -not $cim.CommandLine.Contains($info.entry)) { return $false }
    if (-not $cim.CreationDate) { return $false }
    $recordedCt = $null
    try {
        $recordedCt = [DateTime]::Parse($info.creation_time, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
    } catch { return $false }
    $delta = [Math]::Abs(($cim.CreationDate - $recordedCt).TotalSeconds)
    if ($delta -gt 2) { return $false }
    return $true
}

$Toonflow = Join-Path $Root "vendor\toonflow"
$Bun = Join-Path $Root "vendor\tools\bun-windows-x64\bun.exe"
$Entry = Join-Path $Root "scripts\toonflow-loopback.ts"
$DataDir = Join-Path $Root "runtime\toonflow-data"
$LogDir = Join-Path $Root "runtime\logs"
$InfoFile = Join-Path $DataDir "server-info.json"
$ServerPort = 3000
$McpPort = 10588

foreach ($p in @($Bun, $Entry)) { if (-not (Test-Path $p)) { Write-Error "missing $p (run scripts/deploy.ps1 first)"; exit 1 } }
foreach ($p in @("build\web", "build\mcp\stdio.js", "build\agents\storyboardTeam\team.json")) {
    if (-not (Test-Path (Join-Path $Toonflow $p))) { Write-Error "missing vendor/toonflow/$p (run deploy.ps1 + bun run build:teams first)"; exit 1 }
}
New-Item -ItemType Directory -Force -Path $DataDir, $LogDir, (Join-Path $Root "runtime\tmp") | Out-Null

# 已在运行则直接返回现有 PID（身份校验通过才复用）
if (Test-Path $InfoFile) {
    $info = Get-Content $InfoFile -Raw | ConvertFrom-Json
    $proc = Get-Process -Id $info.pid -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -eq "bun" -and (Confirm-OwnProcess $info $proc)) {
        try { $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$ServerPort/" -UseBasicParsing -TimeoutSec 5 } catch {}
        if ($resp -and $resp.StatusCode -eq 200) { Write-Output $info.pid; exit 0 }
    }
    Remove-Item $InfoFile -Force -ErrorAction SilentlyContinue
}

# 端口冲突预检：3000/10588 若被任何进程占用则拒绝启动（绝不关闭别人的进程）
foreach ($port in @($ServerPort, $McpPort)) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { Write-Error "port $port already listening (owning PID: $($conn[0].OwningProcess)); refusing to start"; exit 1 }
}

# 预置 settings.json（官方数据目录内的 MCP 开关；等价于 UI 设置→MCP 开启）
$SettingsFile = Join-Path $DataDir "settings.json"
$settings = $null
if (Test-Path $SettingsFile) { try { $settings = Get-Content $SettingsFile -Raw | ConvertFrom-Json } catch {} }
if (-not $settings) { $settings = New-Object PSObject }
if (-not $settings.settings) { $settings | Add-Member -NotePropertyName settings -NotePropertyValue (New-Object PSObject) -Force }
$mcp = $settings.settings.mcp
if (-not $mcp) { $settings.settings | Add-Member -NotePropertyName mcp -NotePropertyValue (New-Object PSObject) -Force; $mcp = $settings.settings.mcp }
if (-not $mcp.PSObject.Properties["enabled"] -or $mcp.enabled -ne $true) { $mcp | Add-Member -NotePropertyName enabled -NotePropertyValue $true -Force }
$token = $mcp.token
if (-not $token -or $token.Length -lt 32 -or $token -match '^(00)+$') {
    # PS5.1（.NET Framework）无 [Security.Cryptography.RandomNumberGenerator]::Fill（实测缺失）；[byte[]]::new(24) 实际可用（repair1 更正旧注释）。
    # 用两个随机 GUID（.NET 加密 RNG）拼 64 位 hex token。弱/全零/空 token 一律重生成。
    $token = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $mcp | Add-Member -NotePropertyName token -NotePropertyValue $token -Force
}
if (-not $mcp.PSObject.Properties["port"]) { $mcp | Add-Member -NotePropertyName port -NotePropertyValue $McpPort -Force }
# conf 包 JSON.parse 不容忍 BOM：必须写无 BOM UTF-8（PS5.1 的 Set-Content -Encoding UTF8 带 BOM，不可用）
$json = $settings | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($SettingsFile, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "settings.json provisioned (mcp enabled, port $McpPort, token length $($token.Length))"

# 子进程环境（缓存/临时全部指向目标目录内）
$env:TOONFLOW_DATA_DIR = $DataDir
$env:BUN_INSTALL = Join-Path $Root "vendor\tools\bun"
$env:TMP = Join-Path $Root "runtime\tmp"; $env:TEMP = $env:TMP

$outLog = Join-Path $LogDir "toonflow-server.out.log"
$errLog = Join-Path $LogDir "toonflow-server.err.log"
$proc = Start-Process -FilePath $Bun -ArgumentList "`"$Entry`"" -WorkingDirectory $Toonflow -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
$cimProc = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.Id)" -ErrorAction SilentlyContinue
$creationTime = if ($cimProc.CreationDate) { $cimProc.CreationDate.ToString("o") } else { (Get-Date).ToString("o") }
Write-Host "started bun pid=$($proc.Id) entry=$Entry cmdline=$($cimProc.CommandLine)"

# 等待 HTTP 200（最多 120 秒：源码包装冷启动含插件初始化）与 MCP 运行信息文件
$ok = $false
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 1
    if ($proc.HasExited) { Write-Error "server exited early (code $($proc.ExitCode)); see $errLog"; exit 1 }
    try { $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$ServerPort/" -UseBasicParsing -TimeoutSec 3 } catch {}
    if ($resp -and $resp.StatusCode -eq 200) { $ok = $true; break }
}
if (-not $ok) { Write-Error "server did not answer 200 on 127.0.0.1:$ServerPort within 120s; see logs"; exit 1 }

$mcpRuntimeFile = $null
for ($i = 0; $i -lt 30; $i++) {
    $f = Join-Path $DataDir "mcpRuntime$McpPort.json"
    if (Test-Path $f) { $mcpRuntimeFile = $f; break }
    Start-Sleep -Seconds 1
}

# 回环断言：3000 必须只出现在 127.0.0.1（不得有 0.0.0.0/[::] 监听）
$listen = Get-NetTCPConnection -LocalPort $ServerPort -State Listen -ErrorAction SilentlyContinue
$bad = @($listen | Where-Object { $_.LocalAddress -ne "127.0.0.1" })
if ($bad.Count -gt 0) {
    Write-Error "port $ServerPort listening on non-loopback: $($bad[0].LocalAddress); wrapper contract violated"
    exit 1
}

$info = [ordered]@{
    pid = $proc.Id; port = $ServerPort; started_at = (Get-Date).ToString("o")
    data_dir = $DataDir; entry = $Entry; exe = $Bun; creation_time = $creationTime
    mcp_port = $McpPort; mcp_runtime_file = $mcpRuntimeFile
    log_out = $outLog; log_err = $errLog; loopback_only = $true
}
# server-info.json 供 stop 脚本消费（无 token/密钥），同样写无 BOM UTF-8
[System.IO.File]::WriteAllText($InfoFile, ($info | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "HTTP 200 OK on http://127.0.0.1:$ServerPort (pid $($proc.Id), loopback-only verified); mcpRuntimeFile=$mcpRuntimeFile"
Write-Output $proc.Id
exit 0
