# SDP hidden companions of star-cluster members, the Gaia DR3 trial (STL-020 to STL-023), everything
# logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-companions.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-companions.ps1 -Branch claude/project-thread-lf7jxs
#   powershell -ExecutionPolicy Bypass -File harness\run-companions.ps1 -Protocol uc06
#     the DR3 dry run of protocol UC-06 (STL-044): adds a pass 0 that counts crowding, matches the field on it,
#     and prints the stacked test's verdict and the cluster list's hash. Results in <SDP data>\companions\uc06-dr3.
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
    [switch]$Refetch,
    [ValidateSet("trial", "uc06")][string]$Protocol = "trial",
    [switch]$Isochrones
)
$Folder = if ($Protocol -eq "uc06") { "uc06-dr3" } else { "clusters" }
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

$Reports = Join-Path $Data "reports\companions"
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
        $screenOk = Step "3 Cluster screen ($Protocol)" {
            $a = @("companions", "screen", "--sweep", $script:Name, "--protocol", $script:Protocol, "--name", $script:Folder)
            if ($script:Isochrones) { $a += "--isochrones" }
            Sdp @a | Out-Null
        }
    } else { Write-Host "The field list failed (Gaia BH1 must be flagged), so the cluster screen does not run" }
} else { Write-Host "The orbit download failed, so nothing else runs" }

Pop-Location
$Clusters = Join-Path $Data "companions\$Folder\clusters.csv"
$Candidates = Join-Path $Data "companions\$Folder\candidates.csv"
$ScreenSummary = Join-Path $Data "companions\$Folder\summary.json"
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
if ($screenOk -and $Protocol -eq "uc06" -and (Test-Path $ScreenSummary)) {
    $s = Get-Content $ScreenSummary -Raw | ConvertFrom-Json
    Write-Host "`n=== UC-06 stacked test (DR3 dry run; the verdict counts only on DR4) ==="
    $s.test | Select-Object verdict, decided_by, clusters_in_test, dark_partners, dark_partners_expected, p | Format-List | Out-String | Write-Host
    Write-Host "Wobble excess (reported only):"
    $s.test.wobble | Select-Object wobblers, wobblers_expected, excess_fraction, p | Format-List | Out-String | Write-Host
    Write-Host "Left out (over the size cap): $($s.test.clusters_over_size_cap -join ', ')"
    Write-Host "Clusters out of scope (beyond 1 kpc): $($s.clusters_out_of_scope)"
    Write-Host "Settings frozen hash: $($s.frozen_sha256)"
    Write-Host "Cluster list hash:    $($s.cluster_list_sha256)"
}
Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
if (-not ($have -and $fieldOk -and $screenOk)) { exit 1 }
