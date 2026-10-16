# check-deployment.ps1 - Phase1 部署实际状态检查（真实进程/端口/文件/版本；不做任何生成）
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$script:Fail = 0
function Check($name, $ok, $detail) {
    if ($ok) { Write-Host "[PASS] $name :: $detail" }
    else { Write-Host "[FAIL] $name :: $detail"; $script:Fail++ }
}

$Toonflow = Join-Path $Root "vendor\toonflow"
$Bun = Join-Path $Root "vendor\tools\bun-windows-x64\bun.exe"
$DataDir = Join-Path $Root "runtime\toonflow-data"

# 1 源码锁定
$head = git -C $Toonflow rev-parse HEAD 2>$null
Check "toonflow-commit" ($head -eq "2c7fb257affb65777110fe4c6e60cd3c09187d9b") "HEAD=$head"
$vh = git -C (Join-Path $Root "vendor\vbench") rev-parse HEAD 2>$null
Check "vbench-commit" ($vh -eq "fd18b3d055cb0fc6f066ca90fe2c3c8cbb698490") "HEAD=$vh"

# 2 Bun 版本
$bunV = & $Bun --version 2>$null
Check "bun-version" ($bunV -eq "1.3.14") "bun=$bunV"

# 3 依赖安装 + lock 未改
$nmOk = Test-Path (Join-Path $Toonflow "node_modules")
Check "bun-install-done" $nmOk "node_modules present=$nmOk"
$lockDirty = git -C $Toonflow status --porcelain -- bun.lock
Check "lockfile-unmodified" (-not $lockDirty) "dirty='$lockDirty'"

# 4 构建产物（含 v3 teams 构建输出）
$entry = Join-Path $Toonflow "build\server\index.js"
Check "build-server" (Test-Path $entry) $entry
Check "build-web" (Test-Path (Join-Path $Toonflow "build\web")) "build/web"
$nodesCount = @(Get-ChildItem (Join-Path $Toonflow "build\nodes") -Filter *.umd.js -File -ErrorAction SilentlyContinue).Count
Check "build-nodes" ($nodesCount -ge 5) "node UMD bundles=$nodesCount (expect >=5 incl director3d)"
Check "build-nodes-director3d" (Test-Path (Join-Path $Toonflow "build\nodes\director3dNode.umd.js")) "build/nodes/director3dNode.umd.js"
Check "build-agents-teams" ((Test-Path (Join-Path $Toonflow "build\agents\storyboardTeam\team.json")) -and (Test-Path (Join-Path $Toonflow "build\agents\storyboardTeam.agent.zip"))) "build/agents/storyboardTeam (+.agent.zip, official build:teams)"
Check "teams-installed-in-data" (Test-Path (Join-Path $Root "runtime\toonflow-data\agents\storyboardTeam\team.json")) "data/agents/storyboardTeam (first-install via agentsRoot)"
$stdio = Join-Path $Toonflow "build\mcp\stdio.js"
Check "build-mcp-stdio" (Test-Path $stdio) $stdio

# 5 storyboardTeam 源入口
$teamMembers = @("members\director.md", "members\writer.md", "members\reviewer.md") | ForEach-Object { Join-Path $Toonflow "packages\teams\storyboardTeam\$_" }
$teamOk = ($teamMembers | Where-Object { -not (Test-Path $_) }).Count -eq 0
Check "storyboardteam-source" $teamOk ("director/writer/reviewer present=" + $teamOk)

# 6 服务进程 + HTTP 真实响应 + v3 loopback-only 断言
$InfoFile = Join-Path $DataDir "server-info.json"
$running = $false; $httpDetail = "no server-info.json"
if (Test-Path $InfoFile) {
    $info = Get-Content $InfoFile -Raw | ConvertFrom-Json
    $proc = Get-Process -Id $info.pid -ErrorAction SilentlyContinue
    $running = ($proc -and $proc.ProcessName -eq "bun")
    $httpDetail = "pid=$($info.pid) alive=$running entry=$($info.entry)"
}
Check "server-process" $running $httpDetail
Check "server-entry-wrapper" ((Get-Content $InfoFile -Raw | ConvertFrom-Json).entry -like "*toonflow-loopback.ts") "entry must be scripts/toonflow-loopback.ts (v3)"
$body = ""; $code = 0
try { $resp = Invoke-WebRequest -Uri "http://127.0.0.1:3000/" -UseBasicParsing -TimeoutSec 8; $code = $resp.StatusCode; $body = $resp.Content } catch { $code = 0 }
$hasContent = $body -and $body.Length -gt 200
Check "server-http-200" ($code -eq 200 -and $hasContent) "status=$code bodyLen=$($body.Length)"
$listen = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($listen) {
    $badBind = @($listen | Where-Object { $_.LocalAddress -ne "127.0.0.1" })
    Check "server-port-loopback-only" ($badBind.Count -eq 0) ("binds: " + (($listen | ForEach-Object { $_.LocalAddress }) -join ",") + " (pid $($listen[0].OwningProcess)); non-loopback count=$($badBind.Count)")
} else {
    Check "server-port-loopback-only" $false "port 3000 not listening"
}

# 7 MCP 端口（官方 runtime.ts 绑定 127.0.0.1）
$mcpListen = Get-NetTCPConnection -LocalPort 10588 -State Listen -ErrorAction SilentlyContinue
$mcpLocal = $mcpListen -and ($mcpListen | Where-Object { $_.LocalAddress -eq "127.0.0.1" })
Check "mcp-port-loopback" ([bool]$mcpLocal) ("port 10588 on 127.0.0.1=" + [bool]$mcpLocal)
$mcpRt = Get-ChildItem $DataDir -Filter "mcpRuntime*.json" -ErrorAction SilentlyContinue | Select-Object -First 1
Check "mcp-runtime-file" ([bool]$mcpRt) ("file=" + $mcpRt.Name)

# 8 verify-mcp.mjs 语法
node --check (Join-Path $Root "scripts\verify-mcp.mjs") 2>$null
Check "verify-mcp-syntax" ($LASTEXITCODE -eq 0) "node --check exit=$LASTEXITCODE"

# 9 VBench venv + torch CUDA（真实调用 check-vbench.py）
$vp = Join-Path $Root "runtime\venvs\vbench\Scripts\python.exe"
Check "vbench-venv" (Test-Path $vp) $vp
if (Test-Path $vp) {
    & $vp (Join-Path $Root "scripts\check-vbench.py") --no-download --cuda-only 2>&1 | ForEach-Object { Write-Host "    vbench: $_" }
    Check "vbench-cuda-check" ($LASTEXITCODE -eq 0) "check-vbench.py exit=$LASTEXITCODE"
}

# 10 预算守卫：无权重缓存
$wv = @((Join-Path $Root "runtime\cache\vbench"), (Join-Path $Root "runtime\cache\vbench2")) | Where-Object { Test-Path $_ }
$weightsFound = @($wv | Where-Object { @(Get-ChildItem $_ -Recurse -File -ErrorAction SilentlyContinue).Count -gt 0 })
Check "no-weights-downloaded" ($weightsFound.Count -eq 0) "weight-cache dirs with files: $($weightsFound.Count)"

Write-Host ""
if ($script:Fail -eq 0) { Write-Host "ALL DEPLOYMENT CHECKS PASS"; exit 0 } else { Write-Host "$($script:Fail) CHECK(S) FAILED"; exit 1 }
