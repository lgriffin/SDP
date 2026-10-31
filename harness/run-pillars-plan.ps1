# SDP Pillars of Creation: draft the 2 GB acquisition plan and print it, nothing downloaded (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-pillars-plan.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-pillars-plan.ps1 -Region "icrs;circle(274.7299,-13.8517,0.035)"
#
# 1. Updates the checkout (-Branch, main by default) and registers the archive descriptors
# 2. Drafts the plan for the region under profiles\pillars-of-creation.yaml: it lists MAST's JWST and HST
#    mosaics over the field and reads each one's FITS headers to cost its cutout, but reads no pixels
# 3. Saves the whole plan as JSON and prints what it keeps, what it leaves out and why, and the total
# The region is not approved by this script. Check it, and the plan, before running `sdp plan approve`.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Branch = "main",
    # The 2014 Hubble framing of the three pillars (HST programme 13926's WFC3/UVIS pointing), 90 arcsec radius.
    # Proposed in docs/pillars.md; pass -Region to plan another patch.
    [string]$Region = "icrs;circle(274.7104,-13.8289,0.025)",
    [string]$Actor = "leigh"
)
$ErrorActionPreference = "Continue"

cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\pillars"
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

function GB($Bytes) { return "{0:N3} GB" -f ($Bytes / 1e9) }

Push-Location $Code
git fetch origin
git checkout $Branch
git pull origin $Branch
Set-Location (Join-Path $Code "python")
uv sync --frozen
$ProfilePath = Join-Path $Code "profiles\pillars-of-creation.yaml"
Write-Host "Region: $Region (not yet approved: check it before approving the plan)"

$script:PlanId = $null
Step "1 Register descriptors" { Sdp integration register | Out-Null } | Out-Null
$drafted = Step "2 Draft the plan" {
    $json = Sdp plan draft --region $script:Region --profile $script:ProfilePath --label "Pillars of Creation" --actor $script:Actor
    # uv may print a line of its own before sdp's JSON
    $json = $json.Substring($json.IndexOf("{"), $json.LastIndexOf("}") - $json.IndexOf("{") + 1)
    $script:PlanId = ($json | ConvertFrom-Json).plan_id
}
if ($drafted -and $script:PlanId) {
    $File = Join-Path $Reports "plan-$Stamp.json"
    Step "3 Save the plan" {
        & uv run sdp plan show $script:PlanId | Out-File -Encoding utf8 $File
        if ($LASTEXITCODE -ne 0) { throw "sdp plan show exited $LASTEXITCODE" }
    } | Out-Null
    $plan = (Get-Content $File -Raw | ConvertFrom-Json).document
    Write-Host "`n=== Kept: $($plan.items.Count) items, $(GB $plan.cost.total_bytes) of $(GB $plan.constraints.budget.max_bytes) ==="
    $plan.items | Sort-Object source_id, target | ForEach-Object {
        Write-Host ("{0,-6} {1,12} {2}" -f $_.source_id, (GB $_.expected_bytes), $_.target)
    }
    $excluded = @($plan.constraints.excluded)
    Write-Host "`n=== Left out: $($excluded.Count) products, by reason ==="
    $excluded | Group-Object { ($_.reason -replace "\d+", "N") } | Sort-Object Count -Descending | ForEach-Object {
        Write-Host ("{0,5}  {1}" -f $_.Count, $_.Name)
    }
    $budget = $excluded | Where-Object { $_.reason -like "*nominal budget*" }
    if ($budget) {
        Write-Host "`n=== Left out for the budget ==="
        $budget | ForEach-Object { Write-Host "  $($_.target)" }
    }
    Write-Host "`nPlan id: $($script:PlanId)"
    Write-Host "Requires a signed approval: $($plan.cost.total_bytes -gt $plan.constraints.budget.approval_threshold_bytes)"
    Write-Host "Whole plan: $File"
} else { Write-Host "The plan was not drafted, so there is nothing to show" }

Pop-Location
Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
