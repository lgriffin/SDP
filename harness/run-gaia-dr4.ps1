# SDP Gaia DR4 release run: protocol UC-06 Part A (the 24-cluster delta) and Part B (dark partners in
# clusters), after the release-day mapping is merged (docs/dr4-release-day.md). Everything logged (see
# harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-gaia-dr4.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-gaia-dr4.ps1 -Step copy      # only the bulk copy
#   powershell -ExecutionPolicy Bypass -File harness\run-gaia-dr4.ps1 -SkipCopy       # Part A and the orbits only
#
# Steps, each resumable (run the script again and it carries on):
#   check   `sdp archive gaia-check --release dr4`: stops at once if any DR4 name SDP reads is wrong
#   delta   Part A: DR4 cones of the 24 frozen clusters, then the DR3 -> DR4 comparison
#   orbits  every DR4 orbit with Gaia's masses, then the UC-06 field list (Gaia BH1 must be flagged)
#   copy    the whole DR4 gaia_source into the artifact store (hundreds of GB; days, not hours)
#   screen  Part B: `sdp companions screen --protocol uc06` (DR3 footprints, DR4 members, field and orbits)
# Nothing here changes a predeclared setting: the screen refuses settings that differ from the lock.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Data = "D:\SDP",
    [string]$Branch = "main",
    [ValidateSet("all", "check", "delta", "orbits", "copy", "screen")]
    [string]$Step = "all",
    [switch]$SkipCopy,
    [int]$Workers = 4,
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
$Data = (Resolve-Path -LiteralPath $Data).Path
$env:SDP_DATA = $Data
$Reports = Join-Path $Data "reports\gaia-dr4"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") -Append | Out-Null
Use-RunCheckout

Push-Location $Code
Set-Location (Join-Path $Code "python")
uv sync --frozen
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: uv sync"; Stop-Transcript | Out-Null; Pop-Location; exit 1 }

# PowerShell 5.1's transcript does not record what a native program prints, so sdp goes through Write-Host
function Sdp {
    & uv run sdp @args 2>&1 | ForEach-Object { Write-Host "$_" }
    return $LASTEXITCODE
}
function Want([string]$Name) { return ($Step -eq "all" -or $Step -eq $Name) }
function Retried([string]$What, [scriptblock]$Call) {
    for ($try = 1; $try -le $Tries; $try++) {
        Write-Host "`n=== $What, try $try of $Tries : $(Get-Date -Format HH:mm:ss) ==="
        if ((& $Call) -eq 0) { return $true }
        if ($try -lt $Tries) { Write-Host "Not complete; trying again in two minutes (what is done is kept)"; Start-Sleep -Seconds 120 }
    }
    return $false
}
$failed = @()

Write-Host "`n=== check DR4's names against the archive : $(Get-Date -Format HH:mm:ss) ==="
if ((Sdp archive gaia-check --release dr4) -ne 0) {
    Write-Host "STOP: some DR4 table or column names are wrong, or the archive did not answer (listed above)."
    Write-Host "Nothing was fetched. The names live in python\packages\sdp-adapters\src\sdp\adapters\archives\gaia_releases.yaml;"
    Write-Host "see docs\dr4-release-day.md."
    Stop-Transcript | Out-Null; Pop-Location; exit 1
}

if (Want "delta") {
    if (-not (Retried "Part A: fetch the DR4 cones" { Sdp delta fetch --release dr4 })) { $failed += "delta fetch" }
    Write-Host "`n=== Part A: compare DR3 to DR4 : $(Get-Date -Format HH:mm:ss) ==="
    if ((Sdp delta compare --from dr3 --to dr4) -ne 0) { $failed += "delta compare" }
}

if (Want "orbits") {
    if (-not (Retried "Part B: fetch every DR4 orbit" { Sdp companions fetch --release dr4 })) { $failed += "orbits" }
    Write-Host "`n=== Part B: the UC-06 field list, Gaia BH1 check : $(Get-Date -Format HH:mm:ss) ==="
    if ((Sdp companions field --protocol uc06) -ne 0) { $failed += "field (Gaia BH1)" }
}

if ((Want "copy") -and -not $SkipCopy) {
    Write-Host "`n=== the DR4 bulk copy : $(Get-Date -Format HH:mm:ss) ==="
    Sdp archive bulk --source gaia --release dr4 --status | Out-Null
    if (-not (Retried "copy DR4 gaia_source" { Sdp archive bulk --source gaia --release dr4 --workers $Workers })) {
        $failed += "bulk copy"
    }
}

if ((Want "screen") -and -not $SkipCopy) {
    Write-Host "`n=== Part B: the UC-06 screen (DR3 footprints, DR4 members) : $(Get-Date -Format HH:mm:ss) ==="
    if ((Sdp companions screen --sweep gaia-dr3 --protocol uc06 --isochrones --name uc06-dr4) -ne 0) {
        $failed += "screen"
    } else {
        $summary = Join-Path $Data "companions\uc06-dr4\summary.json"
        if (Test-Path -LiteralPath $summary) {
            $s = Get-Content -Raw -LiteralPath $summary | ConvertFrom-Json
            Write-Host "verdict: $($s.test.verdict)  counts: $($s.test.counts)  not counted because: $($s.test.not_counted_because -join '; ')"
        }
    }
}

Pop-Location
Write-Host "`nResults: $Data\delta (Part A), $Data\companions\field-uc06.csv and $Data\companions\uc06-dr4 (Part B)"
if ($failed.Count -gt 0) {
    Write-Host "Not finished: $($failed -join ', '). Run the script again; every step resumes."
    Stop-Transcript | Out-Null
    exit 1
}
Stop-Transcript | Out-Null
