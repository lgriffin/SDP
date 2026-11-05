# SDP publication linkage (Discovery Roadmap C3, PUB-001..004): which public JWST programs does no paper
# name? Everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-publication.ps1
#
# 1. Makes sure the gitignored .env at the top of the checkout holds ADS_API_TOKEN: if it does not, asks
#    for the token without showing it and writes it there, readable only by you. The token is never
#    printed or logged. To replace a token, delete the ADS_API_TOKEN line from .env and run this again.
# 2. `sdp publication programs`: every JWST program at MAST, each public science program asked about in
#    ADS once. Resumable: answers are kept, and a run stopped by ADS's daily allowance carries on when the
#    script is run again (the next day).

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Branch = "main",
    [string]$Mission = "JWST"
)
$ErrorActionPreference = "Continue"
# PowerShell has read this file before the pull below can change it, so a changed copy is run afresh
$Before = (Get-FileHash -LiteralPath $PSCommandPath).Hash

cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\publication"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Out = Join-Path $Data ("publication\" + $Mission.ToLower())
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"

Push-Location $Code
git fetch origin
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: git fetch"; Pop-Location; exit 1 }
git checkout $Branch
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: git checkout $Branch"; Pop-Location; exit 1 }
git pull origin $Branch
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: git pull"; Pop-Location; exit 1 }
if ((Get-FileHash -LiteralPath $PSCommandPath).Hash -ne $Before) {
    Write-Host "The pull changed this script, so the new version runs now"
    Pop-Location
    $Again = @()
    foreach ($k in $PSBoundParameters.Keys) { $Again += "-$k"; $Again += "$($PSBoundParameters[$k])" }
    & powershell -ExecutionPolicy Bypass -File $PSCommandPath @Again
    exit $LASTEXITCODE
}

# 1. The token, before the transcript starts, so nothing about it can reach the log
$DotEnv = Join-Path $Code ".env"
git check-ignore -q .env
if ($LASTEXITCODE -ne 0) { Write-Host "STOP: .env is not gitignored in $Code, so no token is written"; Pop-Location; exit 1 }
$HasToken = (Test-Path $DotEnv) -and (Select-String -Path $DotEnv -Pattern '^\s*ADS_API_TOKEN\s*=\s*\S' -Quiet)
if (-not $HasToken) {
    $Secure = Read-Host "Paste your NASA ADS token (it will not show)" -AsSecureString
    $Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr) }
    if (-not $Plain.Trim()) { Write-Host "No token given; nothing written"; Pop-Location; exit 1 }
    Add-Content -Path $DotEnv -Value "ADS_API_TOKEN=$($Plain.Trim())" -Encoding ascii
    Remove-Variable Plain, Secure
    icacls $DotEnv /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
    Write-Host "Token saved in $DotEnv (gitignored, readable only by $($env:USERNAME))"
} else {
    Write-Host "ADS token found in $DotEnv"
}

Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") -Append | Out-Null
Set-Location (Join-Path $Code "python")
uv sync --frozen
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: uv sync"; Stop-Transcript | Out-Null; Pop-Location; exit 1 }

# 2. The register
Write-Host "`n=== Publication register ($Mission): started $(Get-Date -Format HH:mm:ss) ==="
$t = [Diagnostics.Stopwatch]::StartNew()
uv run sdp publication programs --mission $Mission --out $Out
$code = $LASTEXITCODE
$t.Stop()
Write-Host "=== $(if ($code -eq 0) { 'ok' } else { 'FAILED' }) after $($t.Elapsed.ToString('hh\:mm\:ss')) ==="

Pop-Location
Write-Host "Results: $Out\unpublished.csv (oldest first), programs.csv and summary.json"
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
if ($code -ne 0) { Write-Host "Run the script again to carry on: ADS answers already received are kept."; exit 1 }
