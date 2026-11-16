# SDP MIST isochrone fits of the sweep's clusters, then the hidden-companion screen with isochrone masses
# (STL-033 to STL-036), everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-isochrones.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-isochrones.ps1 -Branch claude/project-thread-qecien
#
# 1. Updates the checkout (-Branch, main by default) in its own worktree
# 2. Downloads MIST v1.2's isochrones with Gaia photometry from mist.science (about 116 MB) and keeps the
#    columns SDP needs, unless the grid is already there (-Refetch to build it again)
# 3. `sdp isochrones fit --sweep <Name>`: one pass over the bulk Gaia copy, then every sweep cluster fitted
#    (age, [Fe/H], A_V, distance), with a table of its members' masses and a plot each
# 4. `sdp companions screen --sweep <Name> --isochrones` into companions\clusters-mist, unless -SkipCompanions
#    (needs the orbit table from run-companions.ps1)
# 5. Prints the famous clusters' ages against their sanity windows, the best-fitting clusters, and any
#    partner heavier than every living star of its cluster
# A failed step stops the steps after it, and the script exits 1. Nothing is deleted. Results land in
# <SDP data>\isochrones and <SDP data>\companions\clusters-mist.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Branch = "main",
    [string]$Name = "gaia-dr3",
    [switch]$Refetch,
    [switch]$SkipCompanions
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

$Reports = Join-Path $Data "reports\isochrones"
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
Set-Location (Join-Path $Code "python")
Must "uv sync" { uv sync --frozen }

$Grid = Join-Path $Data "isochrones\mist-v1.2-gaia.parquet"
$have = $true
if ($Refetch -or -not (Test-Path $Grid)) {
    $have = Step "1 Fetch the MIST grid" { Sdp isochrones fetch | Out-Null }
} else { Write-Host "MIST grid already at $Grid (pass -Refetch to build it again)" }

$fitOk = $false
$screenOk = $true
$screenRan = $false
if ($have) {
    $fitOk = Step "2 Fit the sweep's clusters" { Sdp isochrones fit --sweep $script:Name --name $script:Name | Out-Null }
    $Orbits = Join-Path $Data "companions\gaia-dr3-orbits.parquet"
    if (-not $fitOk) { Write-Host "The cluster fits failed, so the companion screen does not run" }
    elseif ($SkipCompanions) { Write-Host "Companion screen skipped (-SkipCompanions)" }
    elseif (-not (Test-Path $Orbits)) { Write-Host "No orbit table at $Orbits (run harness\run-companions.ps1 first), so the companion screen does not run" }
    else {
        $screenOk = Step "3 Companion screen with MIST" { Sdp companions screen --sweep $script:Name --isochrones --fits (Join-Path $Data "isochrones\$script:Name\fits.json") --name clusters-mist | Out-Null }
        $screenRan = $screenOk
    }
} else { Write-Host "The MIST download failed, so nothing else runs" }

Pop-Location
$Out = Join-Path $Data "isochrones\$Name"
if ($fitOk -and (Test-Path (Join-Path $Out "summary.json"))) {
    $s = Get-Content (Join-Path $Out "summary.json") -Raw | ConvertFrom-Json
    Write-Host "`n=== Grid: MIST $($s.grid.mist_version), MESA r$($s.grid.mesa_revision), sha256 $($s.grid.source_sha256) ==="
    Write-Host "$($s.fitted) of $($s.clusters) clusters fitted"
    Write-Host "`n=== Famous clusters against their sanity windows (not yet verified windows) ==="
    if ($s.known_answers) { $s.known_answers | Format-Table cluster, fitted_log_age, window_log_age, fitted_feh, window_feh, inside -AutoSize | Out-String -Width 200 | Write-Host }
    else { Write-Host "none of the famous clusters is in this sweep" }
    Write-Host "`n=== Clusters most on one isochrone (largest share of members within 2 errors) ==="
    Import-Csv (Join-Path $Out "clusters.csv") | Where-Object { $_.log_age } |
        Sort-Object { -[double]$_.on_isochrone_share } | Select-Object -First 15 |
        Format-Table cluster_id, matches, members, log_age, age_myr, feh, av, distance_pc, on_isochrone_share -AutoSize |
        Out-String -Width 220 | Write-Host
    Write-Host "`n=== Fits at the edge of the search (the best answer may lie beyond it) ==="
    $edge = @(Import-Csv (Join-Path $Out "clusters.csv") | Where-Object { $_.at_grid_edge })
    if ($edge.Count) { $edge | Format-Table cluster_id, matches, log_age, feh, av, at_grid_edge -AutoSize | Out-String -Width 200 | Write-Host }
    else { Write-Host "none" }
    Write-Host "Plots: $Out\plots"
}
$Mist = Join-Path $Data "companions\clusters-mist\candidates.csv"
if ($screenRan -and (Test-Path $Mist)) {
    Write-Host "`n=== Partners heavier than any living star of their cluster ==="
    $heavy = @(Import-Csv $Mist | Where-Object { $_.heavier_than_living -eq "True" })
    if ($heavy.Count) {
        $heavy | Format-Table cluster_id, source_id, verdict, m1_msun, m1_mist_msun, m2_lower_msun, m2_lower_mist_msun, period_days -AutoSize |
            Out-String -Width 200 | Write-Host
    } else { Write-Host "none" }
}
Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
if (-not ($have -and $fitOk -and $screenOk)) { exit 1 }
