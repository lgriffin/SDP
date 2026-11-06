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
    [string]$To = "dr3",
    [int]$Tries = 3
)
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\run-checkout.ps1")
# runs in its own worktree of -Branch, never switching your checkout (#196); see harness/README.md
Enter-RunCheckout -Code $Code -Branch $Branch -Script $PSCommandPath -Bound $PSBoundParameters
$Code = $RunCheckout.Path

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
Use-RunCheckout

Push-Location $Code
Set-Location (Join-Path $Code "python")
uv sync --frozen
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: uv sync"; Stop-Transcript | Out-Null; Pop-Location; exit 1 }

# The sdp output goes through Write-Host: PowerShell 5.1's transcript does not record what a native
# program prints, so without this the log shows no clusters at all.
function Sdp {
    & uv run sdp @args 2>&1 | ForEach-Object { Write-Host "$_" }
    return $LASTEXITCODE
}

# Gaia's archive answers 503 or 408 now and then; each try fetches only the clusters still missing.
$missing = @()
foreach ($release in @($From, $To)) {
    $ok = $false
    for ($try = 1; $try -le $Tries; $try++) {
        Write-Host "`n=== fetch $release, try $try of $Tries : $(Get-Date -Format HH:mm:ss) ==="
        if ((Sdp delta fetch --release $release) -eq 0) { $ok = $true; break }
        if ($try -lt $Tries) { Write-Host "Some clusters failed; trying them again in a minute"; Start-Sleep -Seconds 60 }
    }
    if (-not $ok) { $missing += $release }
}
# compare uses every cluster fetched in both releases and lists the rest in its summary
Write-Host "`n=== compare $From to $To : $(Get-Date -Format HH:mm:ss) ==="
$compared = (Sdp delta compare --from $From --to $To) -eq 0
Pop-Location
Write-Host "`nResults: $Data\delta\deltas-$From-$To.csv and summary-$From-$To.json"
$failed = ($missing.Count -gt 0) -or (-not $compared)
if ($missing.Count -gt 0) {
    Write-Host "Some clusters are still missing in: $($missing -join ', '). The summary lists them;"
    Write-Host "run the script again later to fetch only those (what was fetched is kept)."
}
if (-not $compared) { Write-Host "compare could not match every cluster; run the script again later." }
Stop-Transcript | Out-Null
if ($failed) { exit 1 }
