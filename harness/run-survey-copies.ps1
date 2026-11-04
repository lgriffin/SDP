# SDP local copies of 2MASS and AllWISE, our columns only (DSI-024), everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-survey-copies.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-survey-copies.ps1 -Only allwise
#   powershell -ExecutionPolicy Bypass -File harness\run-survey-copies.ps1 -Branch claude/project-thread-1pvnyf
#
# 1. Updates the checkout (-Branch, main by default); a pull that changes this script runs the new copy
# 2. 2MASS: IRSA's 92 gzipped PSC files, about 45 GB downloaded and 14 GB kept (-TwoMassWorkers at once;
#    each one needs about 3 GB of scratch space while it is read)
# 3. AllWISE: IRSA's 12,288 Parquet files, our 12 columns read by byte range, about 28 GB read and 23 GB
#    kept, from AWS whenever IRSA does not answer (-WiseWorkers at once)
# 4. Prints how much of each copy is stored. Once a copy is complete, runs read it instead of IRSA.
# Stop it any time (Ctrl+C) and run it again: stored files are skipped and failed ones are tried again.
# Nothing is deleted. The copies land in the artifact store under bulk\2mass and bulk\allwise.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Branch = "main",
    [ValidateSet("both", "2mass", "allwise")]
    [string]$Only = "both",
    [int]$TwoMassWorkers = 3,
    [int]$WiseWorkers = 8
)
$ErrorActionPreference = "Continue"
# PowerShell has read this file before the pull below can change it, so a changed copy is run afresh
$Before = (Get-FileHash -LiteralPath $PSCommandPath).Hash

cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\survey-copies"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
# -Append: a rerun after the pull may start within the same minute and must not erase this record
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") -Append | Out-Null
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
    # streamed, not collected: a sweep prints a line per file, 12,288 of them for AllWISE
    & uv run sdp @args 2>&1 | ForEach-Object { Write-Host "$_" }
    if ($LASTEXITCODE -ne 0) { throw "sdp $($args[0]) $($args[1]) exited $LASTEXITCODE" }
}

function Setup($What, [scriptblock]$Body) {
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $What exited $LASTEXITCODE, so nothing is copied with a stale checkout"
        Stop-Transcript | Out-Null
        exit 1
    }
}

Push-Location $Code
Setup "git fetch" { git fetch origin }
Setup "git checkout $Branch" { git checkout $Branch }
Setup "git pull" { git pull origin $Branch }
if ((Get-FileHash -LiteralPath $PSCommandPath).Hash -ne $Before) {
    Write-Host "The pull changed this script, so the new version runs now"
    Pop-Location
    Stop-Transcript | Out-Null
    $Again = @()
    foreach ($k in $PSBoundParameters.Keys) { $Again += "-$k"; $Again += "$($PSBoundParameters[$k])" }
    & powershell -ExecutionPolicy Bypass -File $PSCommandPath @Again
    exit $LASTEXITCODE
}
Set-Location (Join-Path $Code "python")
Setup "uv sync" { uv sync --frozen }

$Failed = $false
if ($Only -ne "allwise") {
    $ok = Step "1 2MASS copy" { Sdp archive bulk --source 2mass --workers $TwoMassWorkers }
    $Failed = $Failed -or -not $ok
}
if ($Only -ne "2mass") {
    $ok = Step "2 AllWISE copy" { Sdp archive bulk --source allwise --workers $WiseWorkers }
    $Failed = $Failed -or -not $ok
}

Write-Host "`n=== How much is stored ==="
foreach ($source in @("2mass", "allwise")) {
    $doc = (& uv run sdp archive bulk --source $source --status 2>$null | Out-String | ConvertFrom-Json)
    if ($doc) {
        $state = if ($doc.done -eq $doc.files) { "complete: runs read it instead of IRSA" } else { "not complete: runs still ask IRSA; run this script again" }
        Write-Host ("{0,-8} {1,6} of {2,6} files, {3:N0} rows, {4:N1} GB, {5}" -f $source, $doc.done, $doc.files, $doc.rows, ($doc.parquet_bytes / 1e9), $state)
    }
}

Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Pop-Location
if ($Failed) { exit 1 }
