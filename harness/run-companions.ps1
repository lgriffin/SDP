# SDP hidden companions of star-cluster members, the Gaia DR3 trial (STL-020 to STL-023), everything
# logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-companions.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-companions.ps1 -Branch claude/project-thread-lf7jxs
#
# 1. Updates the checkout (-Branch, main by default)
# 2. Downloads every Gaia DR3 orbit with Gaia's masses (about 169,000 rows, 36 parts, about 10 minutes; a rerun
#    after a timeout resumes from the parts already saved), unless the
#    orbit table is already there (-Refetch to download again)
# 3. The field list: every orbit at its own distance; the log says whether Gaia BH1 is flagged (it must be)
# 4. The cluster screen over the bulk Gaia copy, two passes, using the sweep's clusters (-Name)
# 5. Prints the clusters with a dark partner and the most wobblers against the field
# A failed step stops the steps after it, and the script exits 1. Nothing is deleted. Results land in <SDP data>\companions.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Branch = "main",
    [string]$Name = "gaia-dr3",
    [switch]$Refetch
)
$ErrorActionPreference = "Continue"

cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\companions"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") | Out-Null
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

function Must($What, [scriptblock]$Body) {
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $What exited $LASTEXITCODE, so nothing runs (the checkout may be stale)"
        Pop-Location
        Stop-Transcript | Out-Null
        exit 1
    }
}

Push-Location $Code
Must "git fetch" { git fetch origin }
Must "git checkout $Branch" { git checkout $Branch }
Must "git pull" { git pull origin $Branch }
Set-Location (Join-Path $Code "python")
Must "uv sync" { uv sync --frozen }

$Orbits = Join-Path $Data "companions\gaia-dr3-orbits.parquet"
$have = $true
if ($Refetch -or -not (Test-Path $Orbits)) {
    $have = Step "1 Fetch Gaia DR3 orbits" { Sdp companions fetch | Out-Null }
} else { Write-Host "Orbit table already at $Orbits (pass -Refetch to download again)" }

$fieldOk = $false
$screenOk = $false
if ($have) {
    $fieldOk = Step "2 Field list" { Sdp companions field | Out-Null }
    if ($fieldOk) {
        $screenOk = Step "3 Cluster screen" { Sdp companions screen --sweep $script:Name | Out-Null }
    } else { Write-Host "The field list failed (Gaia BH1 must be flagged), so the cluster screen does not run" }
} else { Write-Host "The orbit download failed, so nothing else runs" }

Pop-Location
$Clusters = Join-Path $Data "companions\clusters\clusters.csv"
$Candidates = Join-Path $Data "companions\clusters\candidates.csv"
if ($screenOk -and (Test-Path $Candidates)) {
    Write-Host "`n=== Cluster members with a dark partner ==="
    Import-Csv $Candidates | Where-Object { $_.verdict -in @("neutron-star-or-black-hole", "dark-partner") } |
        Format-Table cluster_id, cluster_matches, source_id, verdict, m2_lower_msun, m1_msun, period_days, seed_member -AutoSize |
        Out-String -Width 200 | Write-Host
}
if ($screenOk -and (Test-Path $Clusters)) {
    Write-Host "`n=== Clusters with the most wobblers against the field (lowest field p first) ==="
    Import-Csv $Clusters | Where-Object { $_.members -and [int]$_.members -gt 0 } |
        Sort-Object { [double]$_.wobblers_field_p } | Select-Object -First 15 |
        Format-Table cluster_id, matches, members, wobblers, wobblers_expected, wobblers_field_p, flagged_orbits, flagged_expected -AutoSize |
        Out-String -Width 200 | Write-Host
}
Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
if (-not ($have -and $fieldOk -and $screenOk)) { exit 1 }
