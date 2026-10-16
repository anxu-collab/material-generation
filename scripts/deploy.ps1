# deploy.ps1 - product-material-pipeline-v1 Phase1 部署编排（幂等；-DryRun 仅校验与计划，零生成零费用）
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Toonflow = Join-Path $Root "vendor\toonflow"
$Vbench = Join-Path $Root "vendor\vbench"
$Bun = Join-Path $Root "vendor\tools\bun-windows-x64\bun.exe"
$ExpectedCommit = "2c7fb257affb65777110fe4c6e60cd3c09187d9b"
$VbenchCommit = "fd18b3d055cb0fc6f066ca90fe2c3c8cbb698490"

function Step($name) { Write-Host "==> $name" }
function Ok($msg)    { Write-Host "    [OK] $msg" }
function Bad($msg)   { Write-Host "    [FAIL] $msg"; $script:Failures++ }

$script:Failures = 0
Write-Host "deploy.ps1  DryRun=$DryRun  cwd=$Root"

# --- 静态前置校验（DryRun 与真实模式共用） ---
Step "Verify Toonflow v2.0.5 source pin"
if (Test-Path (Join-Path $Toonflow ".git")) {
    $head = git -C $Toonflow rev-parse HEAD 2>$null
    if ($head -eq $ExpectedCommit) { Ok "commit $head" } else { Bad "HEAD=$head expected=$ExpectedCommit" }
} else { Bad "vendor/toonflow missing (run: git clone --depth 1 --branch v2.0.5 https://github.com/HBAI-Ltd/Toonflow-app vendor/toonflow)" }

Step "Verify Bun 1.3.14 (project-local)"
if (Test-Path $Bun) {
    $v = & $Bun --version
    if ($v -eq "1.3.14") { Ok "bun $v at $Bun" } else { Bad "bun=$v expected 1.3.14" }
} else { Bad "bun.exe missing under vendor/tools" }

Step "Verify VBench source pin"
if (Test-Path (Join-Path $Vbench ".git")) {
    $vh = git -C $Vbench rev-parse HEAD 2>$null
    if ($vh -eq $VbenchCommit) { Ok "commit $vh" } else { Bad "HEAD=$vh expected=$VbenchCommit" }
} else { Bad "vendor/vbench missing" }

Step "Verify dependency lock respected"
if (Test-Path (Join-Path $Toonflow "bun.lock")) { Ok "bun.lock present" } else { Bad "bun.lock missing" }
$lockDirty = git -C $Toonflow status --porcelain -- bun.lock
if ($lockDirty) { Bad "bun.lock modified: $lockDirty" } else { Ok "bun.lock unmodified" }

if ($DryRun) {
    Step "DryRun plan (NOT executed)"
    Write-Host "    1. bun install --frozen-lockfile   (if node_modules absent)"
    Write-Host "    2. bun run build:server            (if build/server/index.js absent)"
    Write-Host "    3. bun run build:mcp               (if build/mcp/stdio.js absent)"
    Write-Host "    4. bun run build:teams             (if build/agents/storyboardTeam absent)"
    Write-Host "    5. scripts/start-toonflow.ps1      (if server not running; loopback wrapper)"
    Write-Host "    6. scripts/check-deployment.ps1"
    Write-Host "    Budget: paid generation 0, model weights 0, everything loopback-local."
    if ($script:Failures -gt 0) { exit 1 } else { Write-Host "DRYRUN PASS"; exit 0 }
}

# --- 真实模式（只补缺失步骤，全部本地零费用） ---
$env:BUN_INSTALL = Join-Path $Root "vendor\tools\bun"
$env:BUN_INSTALL_CACHE_DIR = Join-Path $Root "runtime\cache\bun-install"
$env:TMP = Join-Path $Root "runtime\tmp"; $env:TEMP = $RootTmp = $env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null

Step "bun install --frozen-lockfile"
if (-not (Test-Path (Join-Path $Toonflow "node_modules"))) {
    Push-Location $Toonflow
    & $Bun install --frozen-lockfile
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Bad "bun install exit=$code"; exit 1 } else { Ok "installed" }
} else { Ok "node_modules present, skip" }

Step "bun run build:server"
if (-not (Test-Path (Join-Path $Toonflow "build\server\index.js"))) {
    Push-Location $Toonflow
    & $Bun run build:server
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Bad "build:server exit=$code"; exit 1 } else { Ok "built" }
} else { Ok "build/server present, skip" }

Step "bun run build:mcp (stdio entry)"
if (-not (Test-Path (Join-Path $Toonflow "build\mcp\stdio.js"))) {
    Push-Location $Toonflow
    & $Bun run build:mcp
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Bad "build:mcp exit=$code" } else { Ok "built" }
} else { Ok "build/mcp/stdio.js present, skip" }

Step "bun run build:teams (v3: storyboardTeam build/agents + .agent.zip)"
if (-not (Test-Path (Join-Path $Toonflow "build\agents\storyboardTeam\team.json"))) {
    Push-Location $Toonflow
    & $Bun run build:teams
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Bad "build:teams exit=$code" } else { Ok "built" }
} else { Ok "build/agents/storyboardTeam present, skip" }

Step "Start Toonflow server (hidden, loopback-local)"
$started = & (Join-Path $PSScriptRoot "start-toonflow.ps1")
if ($LASTEXITCODE -ne 0) { Bad "start-toonflow failed" } else { Ok "server pid=$started" }

Step "Post checks"
& (Join-Path $PSScriptRoot "check-deployment.ps1")
if ($LASTEXITCODE -ne 0) { Bad "check-deployment reported failures" } else { Ok "all deployment checks passed" }

if ($script:Failures -gt 0) { Write-Host "DEPLOY FAILED"; exit 1 } else { Write-Host "DEPLOY OK"; exit 0 }
