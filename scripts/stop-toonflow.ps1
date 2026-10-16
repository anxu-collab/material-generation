# stop-toonflow.ps1 - 仅停止本项目 start-toonflow.ps1 登记且身份门禁完全通过的 bun 进程；不动任何其它进程。
# R1 加固（repair1）：登记记录的 entry/exe/creation_time 与进程实际 exe/命令行/创建时间元数据全部必填必验；
# 缺失/为空/不可解析/不可得一律拒绝（fail closed）；登记 exe/entry 必须等于本项目规范 Bun 与 wrapper 路径，
# 不接受任意 JSON 冒充。任一不符（如 PID 被系统复用给同名的其它 bun）一律拒绝。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$InfoFile = Join-Path $Root "runtime\toonflow-data\server-info.json"

# === IDENTITY FUNCTIONS BEGIN（测试按标记原样提取真实函数调用；勿改标记行）===
function Test-RecordIdentity {
    # 登记记录侧门禁：字段齐全、非空、可解析，且 exe/entry 等于规范项目路径。返回 $null=通过，字符串=拒绝原因。
    param($info, $CanonicalExe, $CanonicalEntry)
    if (-not $info) { return "server-info record is null/unparseable; refusing (fail closed)" }
    if (-not $info.PSObject.Properties["pid"] -or -not ($info.pid -is [int] -or $info.pid -is [long]) -or $info.pid -le 0) {
        return "record pid missing/invalid; refusing (identity incomplete)"
    }
    foreach ($p in @("exe", "entry", "creation_time")) {
        $prop = $info.PSObject.Properties[$p]
        if (-not $prop -or -not ($prop.Value -is [string]) -or [string]::IsNullOrWhiteSpace($prop.Value)) {
            return "record field '$p' missing/empty; refusing (identity incomplete)"
        }
    }
    try {
        [void][DateTime]::Parse($info.creation_time, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
    } catch {
        return "record creation_time unparseable '$($info.creation_time)'; refusing (fail closed)"
    }
    $canonExe = try { (Resolve-Path -LiteralPath $CanonicalExe -ErrorAction Stop).Path } catch { return "canonical bun not found '$CanonicalExe'; refusing (fail closed)" }
    $canonEntry = try { (Resolve-Path -LiteralPath $CanonicalEntry -ErrorAction Stop).Path } catch { return "canonical wrapper not found '$CanonicalEntry'; refusing (fail closed)" }
    $recExe = try { (Resolve-Path -LiteralPath $info.exe -ErrorAction Stop).Path } catch { return "record exe path unavailable '$($info.exe)'; refusing (fail closed)" }
    $recEntry = try { (Resolve-Path -LiteralPath $info.entry -ErrorAction Stop).Path } catch { return "record entry path unavailable '$($info.entry)'; refusing (fail closed)" }
    if ($recExe -ne $canonExe) { return "recorded exe '$recExe' is not the canonical project bun '$canonExe'; refusing (forged record)" }
    if ($recEntry -ne $canonEntry) { return "recorded entry '$recEntry' is not the canonical project wrapper '$canonEntry'; refusing (forged record)" }
    return $null
}

function Test-OwnProcess {
    # 进程侧门禁：实际 exe 路径 + 命令行包含 entry + 创建时间一致；Win32 元数据不可得同样拒绝。返回 $null=通过，字符串=拒绝原因。
    param($info, $proc, $CanonicalExe, $CanonicalEntry)
    if ($proc.ProcessName -ne "bun") { return "pid $($info.pid) is '$($proc.ProcessName)', not bun; refusing (not ours)" }
    if (-not $proc.Path) { return "pid $($info.pid) exe path unavailable (process metadata denied); refusing (fail closed)" }
    $procPath = try { (Resolve-Path -LiteralPath $proc.Path -ErrorAction Stop).Path } catch { $proc.Path }
    $infoPath = try { (Resolve-Path -LiteralPath $info.exe -ErrorAction Stop).Path } catch { $info.exe }
    if ($procPath -ne $infoPath) { return "pid $($info.pid) exe '$procPath' != recorded '$infoPath'; refusing (same-name different binary)" }
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.Id)" -ErrorAction SilentlyContinue
    if (-not $cim) { return "pid $($info.pid) Win32_Process metadata unavailable; refusing (fail closed)" }
    if (-not $cim.CommandLine) { return "pid $($info.pid) command line unavailable/empty; refusing (fail closed)" }
    if (-not $cim.CommandLine.Contains($info.entry)) {
        return "pid $($info.pid) command line does not contain entry '$($info.entry)'; refusing (same-exe different entry)"
    }
    if (-not $cim.CreationDate) { return "pid $($info.pid) creation time unavailable; refusing (fail closed)" }
    $recordedCt = $null
    try {
        $recordedCt = [DateTime]::Parse($info.creation_time, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
    } catch {
        return "pid $($info.pid) recorded creation_time unparseable; refusing (fail closed)"
    }
    $delta = [Math]::Abs(($cim.CreationDate - $recordedCt).TotalSeconds)
    if ($delta -gt 2) { return "pid $($info.pid) creation time differs by $([math]::Round($delta,1))s; refusing (stale PID reuse)" }
    return $null
}
# === IDENTITY FUNCTIONS END ===

$CanonicalExe = Join-Path $Root "vendor\tools\bun-windows-x64\bun.exe"
$CanonicalEntry = Join-Path $Root "scripts\toonflow-loopback.ts"

if (-not (Test-Path $InfoFile)) { Write-Host "no server-info.json; nothing recorded as running by this project"; exit 0 }

$info = $null
try { $info = Get-Content $InfoFile -Raw | ConvertFrom-Json } catch { Write-Error "server-info.json unparseable; refusing to act on it (fail closed): $($_.Exception.Message)"; exit 1 }
if (-not $info -or -not $info.pid) { Write-Error "server-info.json has no usable pid; refusing (fail closed)"; exit 1 }
$proc = Get-Process -Id $info.pid -ErrorAction SilentlyContinue
if ($proc) {
    $reason = Test-RecordIdentity $info $CanonicalExe $CanonicalEntry
    if (-not $reason) { $reason = Test-OwnProcess $info $proc $CanonicalExe $CanonicalEntry }
    if ($reason) { Write-Error $reason; exit 1 }
    Stop-Process -Id $info.pid -Force
    $proc.WaitForExit(10000) | Out-Null
    Write-Host "stopped bun pid $($info.pid) (identity verified: exe+entry+creation time, all mandatory)"
} else {
    Write-Host "pid $($info.pid) already gone"
}

# 校验端口释放（仅报告，不动他人进程）
Start-Sleep -Seconds 1
foreach ($port in @($info.port, $info.mcp_port)) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { Write-Host "note: port $port still listening (pid $($conn[0].OwningProcess))" }
}
Remove-Item $InfoFile -Force -ErrorAction SilentlyContinue
Write-Host "server-info.json removed"
exit 0
