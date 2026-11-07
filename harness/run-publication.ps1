# SDP publication linkage (Discovery Roadmap C3, PUB-001..004): which public JWST programs does no paper
# name? Everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-publication.ps1
#
# 1. Finds the NASA ADS token, before anything else, in this order: -AdsToken (a SecureString, from a
#    PowerShell session), ADS_API_TOKEN in the environment, then the gitignored .env at the top of your
#    checkout. With none of them it stops at once and says which line to add to .env (#197), so it can run
#    unattended. -AskToken asks for it instead, without showing it, and saves it in .env readable only by
#    you. The token is never printed or logged.
# 2. `sdp publication programs`: every JWST program at MAST, each public science program asked about in
#    ADS once. Resumable: answers are kept, and a run stopped by ADS's daily allowance carries on when the
#    script is run again (the next day).

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    # where results go when neither the env file nor the shell sets SDP_DATA
    [string]$Data = "D:\SDP",
    [string]$Branch = "main",
    [string]$Mission = "JWST",
    # the ADS token, when it is in neither the environment nor .env
    [System.Security.SecureString]$AdsToken,
    # ask for the token (hidden) when none is found, and save it in .env; without this the script stops
    [switch]$AskToken
)
$ErrorActionPreference = "Continue"

# 1. The token, before git, the worktree or the transcript, so a run that cannot work stops at once and
# nothing about the token reaches the log. It reaches sdp through the environment (ADS_API_TOKEN), which
# the worktree's copy of this script inherits; the worktree has no .env of its own.
function Plain([System.Security.SecureString]$Secure) {
    $Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr) }
}
$DotEnv = Join-Path $Code ".env"
if ($AdsToken) {
    $env:ADS_API_TOKEN = Plain $AdsToken
    Write-Host "ADS token from -AdsToken"
} elseif ($env:ADS_API_TOKEN -or $env:ADS_DEV_KEY) {
    Write-Host "ADS token from the environment"
} else {
    $line = if (Test-Path -LiteralPath $DotEnv) { Select-String -LiteralPath $DotEnv -Pattern '^\s*(?:export\s+)?ADS_API_TOKEN\s*=\s*(\S.*)$' | Select-Object -First 1 } else { $null }
    if ($line) {
        $env:ADS_API_TOKEN = $line.Matches[0].Groups[1].Value.Trim().Trim('"', "'")
        Write-Host "ADS token found in $DotEnv"
    } elseif ($AskToken) {
        git -C $Code check-ignore -q .env
        if ($LASTEXITCODE -ne 0) { Write-Host "STOP: .env is not gitignored in $Code, so no token is written"; exit 1 }
        $Given = Plain (Read-Host "Paste your NASA ADS token (it will not show)" -AsSecureString)
        if (-not $Given) { Write-Host "No token given; nothing written"; exit 1 }
        Add-Content -LiteralPath $DotEnv -Value "ADS_API_TOKEN=$Given" -Encoding ascii
        icacls $DotEnv /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
        $env:ADS_API_TOKEN = $Given
        Remove-Variable Given
        Write-Host "Token saved in $DotEnv (gitignored, readable only by $($env:USERNAME))"
    } else {
        Write-Host "STOP: no NASA ADS token. Add this line to $DotEnv (gitignored):"
        Write-Host "    ADS_API_TOKEN=<your token from https://ui.adsabs.harvard.edu/user/settings/token>"
        Write-Host "or set ADS_API_TOKEN in the environment, or run again with -AskToken to be asked for it."
        exit 1
    }
}

. (Join-Path $PSScriptRoot "lib\run-checkout.ps1")
# runs in its own worktree of -Branch, never switching your checkout (#196); see harness/README.md
Enter-RunCheckout -Code $Code -Branch $Branch -Script $PSCommandPath -Bound $PSBoundParameters -Drop @("AdsToken", "AskToken")
$Code = $RunCheckout.Path

# SDP_DATA comes from the env file when there is one, else the shell, else -Data (D:\SDP); nothing else
# this script runs needs the database or the other variables that file sets
# the token chosen above wins over one the env file may also set
$Chosen = $env:ADS_API_TOKEN
if (Test-Path -LiteralPath $EnvFile) {
    cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
        if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
    }
} else {
    Write-Host "No $EnvFile; using SDP_DATA from the shell or $Data"
}
if ($Chosen) { $env:ADS_API_TOKEN = $Chosen }
Remove-Variable Chosen
# an explicit -Data wins over the env file and the shell
if ($PSBoundParameters.ContainsKey("Data") -or -not $env:SDP_DATA) { $env:SDP_DATA = $Data }
$Data = $env:SDP_DATA
New-Item -ItemType Directory -Force -Path $Data | Out-Null
# an absolute path, so the folders below do not move when the script changes directory
$Data = (Resolve-Path -LiteralPath $Data).Path
$env:SDP_DATA = $Data
$Reports = Join-Path $Data "reports\publication"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Out = Join-Path $Data ("publication\" + $Mission.ToLower())
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"

Push-Location $Code
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") -Append | Out-Null
Use-RunCheckout
Set-Location (Join-Path $Code "python")
uv sync --frozen
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: uv sync"; Stop-Transcript | Out-Null; Pop-Location; exit 1 }

# 2. The register
Write-Host "`n=== Publication register ($Mission): started $(Get-Date -Format HH:mm:ss) ==="
$t = [Diagnostics.Stopwatch]::StartNew()
# through Write-Host, because PowerShell 5.1's transcript does not record what a native program prints
& uv run sdp publication programs --mission $Mission --out $Out 2>&1 | ForEach-Object { Write-Host "$_" }
$code = $LASTEXITCODE
$t.Stop()
Write-Host "=== $(if ($code -eq 0) { 'ok' } else { 'FAILED' }) after $($t.Elapsed.ToString('hh\:mm\:ss')) ==="

Pop-Location
Write-Host "Results: $Out\unpublished.csv (oldest first), programs.csv and summary.json"
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
if ($code -ne 0) { Write-Host "Run the script again to carry on: ADS answers already received are kept."; exit 1 }
