# SDP claim audit (STL-024): do clusters only one catalogue lists survive SDP's own tests? Everything
# logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-claim-audit.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-claim-audit.ps1 -Branch claude/project-thread-8c2bfy
#
# 1. Fetches the three catalogues (hr23, cga20 and the CWNU 2023 list), skipping any already stored
#    with a parallax column (a copy without one is fetched again: every claim in it would be unassessable)
# 2. A 20-claim trial, to time one claim
# 3. The whole audit (resumable: run the script again to carry on), then the report
# Needs the prepared sweep catalogue (`sdp discover prepare`). Run it on its own, not beside another
# script: the audit uses every worker it is given.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    # main once the audit PR is merged; the PR branch to try it before
    [string]$Branch = "main",
    [int]$Workers = 12,
    [string]$Sweep = "gaia-dr3",
    # claims2: the first run (claims) read hr23 without its parallaxes, so its folder cannot be reused
    [string]$Name = "claims2"
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

$Reports = Join-Path $Data "reports\audit"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
# -Append: a rerun after the pull may start within the same minute and must not erase this record
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") -Append | Out-Null
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

function Setup($What, [scriptblock]$Body) {
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $What exited $LASTEXITCODE, so nothing is audited on a stale checkout"
        Stop-Transcript | Out-Null
        exit 1
    }
}

Push-Location $Code
Set-Location (Join-Path $Code "python")
Setup "uv sync" { uv sync --frozen }

$fetched = Step "1 Catalogues" {
    $stored = (Sdp benchmark list | Out-String | ConvertFrom-Json)
    foreach ($id in @("hr23", "cga20", "cwnu23")) {
        $row = $stored | Where-Object { $_.benchmark -eq $id }
        if ($row -and $row.artifact -and $row.columns.parallax_mas) { Write-Host "$id already stored"; continue }
        Sdp benchmark fetch --catalogue $id | Out-Null
    }
}
if ($fetched) {
    $trial = Step "2 Trial (20 claims)" { Sdp discover audit --name $script:Name --sweep $script:Sweep --limit 20 --workers $Workers | Out-Null }
    $whole = $false
    if ($trial) {
        $whole = Step "3 Whole audit" { Sdp discover audit --name $script:Name --sweep $script:Sweep --workers $Workers | Out-Null }
    }
    # after a whole audit that stopped part way the report still runs and says how many claims are
    # done; after a failed trial nothing new was audited, so there is nothing to report
    $report = $false
    if ($trial) {
        $report = Step "4 Report" { Sdp discover audit-report --name $script:Name | Out-Null }
    }
    $failed = -not ($trial -and $whole -and $report)
} else {
    $failed = $true
    Write-Host "A catalogue could not be fetched. If VizieR is down, save the table as a VOTable from any VizieR"
    Write-Host "page and run: uv run sdp benchmark fetch --file cwnu23=PATH (see docs/guides/discovery-search.md)"
}

Pop-Location
Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
Write-Host "Results: $Data\audit\$Name\audit.csv (single claims first) and catalogue-agreement.csv"
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
if ($failed) {
    Write-Host "A step failed. Run the script again to carry on: claims already done are kept."
    exit 1
}
