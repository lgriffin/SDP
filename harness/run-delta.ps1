# SDP Gaia release delta for the 24 seed clusters (Discovery Roadmap C5, STL-030..032). Everything logged
# (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-delta.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-delta.ps1 -From dr3 -To dr4     # from 2 Dec 2026
#
# Fetches each cluster's stars in both releases (kept; a rerun skips what is kept), then compares them.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Data = "D:\SDP",
    [string]$Branch = "main",
    [string]$From = "dr2",
    [string]$To = "dr3"
)
$ErrorActionPreference = "Continue"
# PowerShell has read this file before the pull below can change it, so a changed copy is run afresh
$Before = (Get-FileHash -LiteralPath $PSCommandPath).Hash

if (Test-Path -LiteralPath $EnvFile) {
    cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
        if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
    }
}
if ($PSBoundParameters.ContainsKey("Data") -or -not $env:SDP_DATA) { $env:SDP_DATA = $Data }
$Data = $env:SDP_DATA
New-Item -ItemType Directory -Force -Path $Data | Out-Null
# an absolute path, so the folders below do not move when the script changes directory
$Data = (Resolve-Path -LiteralPath $Data).Path
$env:SDP_DATA = $Data
$Reports = Join-Path $Data "reports\delta"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") -Append | Out-Null

Push-Location $Code
foreach ($step in @(@("fetch", "origin"), @("checkout", $Branch), @("pull", "origin", $Branch))) {
    git @step
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: git $($step -join ' ')"; Stop-Transcript | Out-Null; Pop-Location; exit 1 }
}
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
uv sync --frozen
$failed = $LASTEXITCODE -ne 0
foreach ($release in @($From, $To)) {
    if ($failed) { break }
    Write-Host "`n=== fetch $release : $(Get-Date -Format HH:mm:ss) ==="
    uv run sdp delta fetch --release $release
    $failed = $LASTEXITCODE -ne 0
}
if (-not $failed) {
    Write-Host "`n=== compare $From to $To ==="
    uv run sdp delta compare --from $From --to $To
    $failed = $LASTEXITCODE -ne 0
}
Pop-Location
Write-Host "Results: $Data\delta\deltas-$From-$To.csv and summary-$From-$To.json"
Stop-Transcript | Out-Null
if ($failed) { Write-Host "A step failed. Run the script again: what was fetched is kept."; exit 1 }
