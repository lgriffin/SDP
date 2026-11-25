# SDP real-data checks on the PC (T8R, TST-011 and TST-012), everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-realdata.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-realdata.ps1 -Screen -Sweep
#
# 1. Updates the checkout (-Branch, main by default)
# 2. -Sweep: harness\run-sweep-rerun.ps1 (about 1.5 hours; keeps the last tiles under a dated name). Without it,
#    the last sweep's tiles are used.
# 3. -Screen: sdp companions screen over the bulk copy into companions\realdata-<date> (about 45 minutes, about
#    24 GB of memory). Without it, the last screen's summary (companions\clusters) is measured instead.
# 4. Exports the sweep tile of each of -Clusters (the nearby clusters of issue #170) to <SDP data>\realdata\sweep-tiles
# 5. pytest -m realdata with SDP_REALDATA = <SDP data>: the 169,227 Gaia DR3 orbits at
#    <SDP data>\companions\gaia-dr3-orbits.parquet must give back the 4 November field list exactly, and each
#    exported tile, swept again, must give back what the sweep wrote. A missing table is skipped, saying where it looked.
# 6. sdp companions field: the field list again (seconds)
# 7. Measures known clusters recovered, the null false-positive rate and the companion counts, and checks them
#    against realdata\baselines.json: a value worse than its mark fails the run. Marks are not moved here.
# Nothing is deleted. Writes measured-<date>.json beside the log; paste the log's last block into the thread.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Branch = "main",
    [string]$Name = "gaia-dr3",
    [int]$Workers = 12,
    [switch]$Screen,
    [switch]$Sweep,
    [string[]]$Clusters = @("NGC 2547", "IC 2391", "NGC 6475")
)
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\run-checkout.ps1")
# runs in its own worktree of -Branch, never switching your checkout (#196); see harness/README.md
Enter-RunCheckout -Code $Code -Branch $Branch -Script $PSCommandPath -Bound $PSBoundParameters
$Code = $RunCheckout.Path

cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\realdata"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") | Out-Null
Use-RunCheckout
$Summary = @()

function Step($Name, [scriptblock]$Body) {
    Write-Host "`n=== ${Name}: started $(Get-Date -Format HH:mm:ss) ==="
    $t = [Diagnostics.Stopwatch]::StartNew()
    $ok = $true
    try { & $Body } catch { $ok = $false; Write-Host "FAILED: $_" }
    $t.Stop()
    $line = "{0,-34} {1,-6} {2}" -f $Name, ($(if ($ok) { "ok" } else { "FAILED" })), $t.Elapsed.ToString("hh\:mm\:ss")
    Write-Host "=== $line ==="
    $script:Summary += $line
    return $ok
}

function Sdp {
    $out = @(& uv run sdp @args 2>&1 | ForEach-Object { $s = "$_"; Write-Host $s; $s })
    if ($LASTEXITCODE -ne 0) { throw "sdp $($args[0]) $($args[1]) exited $LASTEXITCODE" }
    return ($out -join "`n")
}

function Native($What, [scriptblock]$Body) {
    & $Body 2>&1 | ForEach-Object { Write-Host "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$What exited $LASTEXITCODE" }
}

Push-Location (Join-Path $Code "python")
Native "uv sync" { uv sync --frozen }

$env:SDP_REALDATA = $Data
$ok = @{}
if ($Sweep) {
    $ok.sweep = Step "1 Sweep rerun" {
        & (Join-Path $PSScriptRoot "run-sweep-rerun.ps1") -Code $Code -EnvFile $EnvFile -Branch $Branch -Workers $Workers -Name $Name
        if ($LASTEXITCODE -ne 0) { throw "run-sweep-rerun.ps1 exited $LASTEXITCODE" }
    }
}
$ScreenFolder = "clusters"
if ($Screen) {
    $ScreenFolder = "realdata-$Stamp"
    $ok.screen = Step "2 Cluster screen" {
        Sdp companions screen --sweep $script:Name --name $script:ScreenFolder | Out-Null
    }
}
# the tiles holding the nearby clusters of issue #170, exported so pytest can sweep them again
$TileOut = Join-Path $Data "realdata\sweep-tiles"
if (Test-Path (Join-Path $Data "sweep\$Name\tiles")) {
    foreach ($cluster in $Clusters) {
        $ok["export $cluster"] = Step "3 Export tile of $cluster" {
            Native "export" { uv run python tools/realdata_slice.py sweep-cluster $cluster $TileOut --sweep $script:Name }
        }
    }
} else { Write-Host "No sweep tiles yet, so no tile is exported" }
$ok.pytest = Step "4 pytest -m realdata" {
    Native "pytest" { uv run pytest -q -m realdata -rs -p no:cacheprovider }
}
$ok.field = Step "5 Field list" { Sdp companions field | Out-Null }

$Measured = Join-Path $Reports "measured-$Stamp.json"
$m = @("run", "python", "tools/realdata.py", "measure", $Measured)
$tiles = Join-Path $Data "sweep\$Name\tiles"
$field = Join-Path $Data "companions\field.csv"
$screenSummary = Join-Path $Data "companions\$ScreenFolder\summary.json"
if (Test-Path $tiles) { $m += @("--sweep", $tiles) } else { Write-Host "No sweep tiles at ${tiles}: the sweep is not measured" }
if (Test-Path $field) { $m += @("--field", $field) } else { Write-Host "No field list at $field" }
if (Test-Path $screenSummary) { $m += @("--screen", $screenSummary) } else { Write-Host "No screen summary at $screenSummary" }
$ok.measure = Step "6 Measure" { Native "measure" { uv @m } }
$ok.ratchet = Step "7 Check against the marks" {
    Native "ratchet" { uv run python tools/realdata.py check $Measured }
}
Pop-Location

Write-Host "`n=== Real-data result (paste this block into the thread) ==="
Write-Host "commit $(git -C $Code rev-parse --short HEAD) on $Branch, $Stamp"
if (Test-Path $Measured) { Get-Content $Measured | Write-Host }
$Summary | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
if ($ok.Values -contains $false) { exit 1 }
