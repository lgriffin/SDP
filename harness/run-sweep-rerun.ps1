# SDP whole-sky sweep rerun after the #159 fix (PR #161), everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-sweep-rerun.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-sweep-rerun.ps1 -Branch claude/project-thread-nu63gl
#
# 1. Moves the last sweep's tiles and candidates.csv aside (renamed with a date, nothing deleted)
# 2. Band 57 (the Pleiades band) as a quick check
# 3. The full sweep, then the report
# 4. Counts known / potentially new against candidates-before.csv (the single-scale sweep)
# Run it on its own, not beside run-idle-pc.ps1: the sweep uses every worker it is given.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    # main once #161 is merged; the PR branch to try it before
    [string]$Branch = "main",
    [int]$Workers = 12,
    [string]$Name = "gaia-dr3"
)
$ErrorActionPreference = "Continue"

cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\sweep"
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

function Counts($Csv) {
    if (-not (Test-Path $Csv)) { return "missing" }
    $rows = Import-Csv $Csv
    $by = $rows | Group-Object status | ForEach-Object { "$($_.Name) $($_.Count)" }
    return ($by -join ", ")
}

Push-Location $Code
git fetch origin
git checkout $Branch
git pull origin $Branch
Set-Location (Join-Path $Code "python")
uv sync --frozen

$Sweep = Join-Path $Data "sweep\$Name"
# the sweep refuses to add tiles to a folder made with other settings, so the last run moves aside
foreach ($item in @("tiles", "candidates.csv")) {
    $path = Join-Path $Sweep $item
    if (Test-Path $path) {
        $leaf = [IO.Path]::GetFileNameWithoutExtension($item) + "-$Stamp" + [IO.Path]::GetExtension($item)
        Rename-Item -Path $path -NewName $leaf
        Write-Host "Moved $path to $leaf"
    }
}

$band = Step "1 Band 57 check" { Sdp discover sweep --name $script:Name --bands 57 --workers $Workers | Out-Null }
if ($band) {
    Step "2 Whole-sky sweep" { Sdp discover sweep --name $script:Name --workers $Workers | Out-Null } | Out-Null
    Step "3 Report" { Sdp discover report --name $script:Name | Out-Null } | Out-Null
} else { Write-Host "Band 57 failed, so the whole-sky sweep is not started" }

Pop-Location
Write-Host "`n=== Candidates by status ==="
Write-Host ("before (single scale):  " + (Counts (Join-Path $Sweep "candidates-before.csv")))
Write-Host ("now:                    " + (Counts (Join-Path $Sweep "candidates.csv")))
Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
