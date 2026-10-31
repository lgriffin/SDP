# SDP multi-epoch change detection on one field (INT-025), everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-epoch-change.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-epoch-change.ps1 -Region "icrs;circle(274.7104,-13.8289,0.008)"
#   powershell -ExecutionPolicy Bypass -File harness\run-epoch-change.ps1 -Branch claude/project-thread-czj8ga
#
# 1. Updates the checkout (-Branch, main by default)
# 2. Drafts the plan for the field under profiles\image-changes.yaml and prints every product and its size
# 3. Stops there if the plan is over -MaxMB (nothing is fetched), else approves and executes it
# 4. Prints each star's verdict (steady, changed, appeared, vanished), the images compared in each filter,
#    their alignment, FWHM and zero point, and the images left out and why
#
# The default field is a 29-arcsecond patch at the 2014 Hubble framing of the Pillars of Creation (M16),
# where WFC3/IR imaged F110W and F160W in 2014 (programme 13926) and again in 2018 (programme 15238).
# Nothing is deleted; results land in SDP's database like any other investigation.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Branch = "main",
    [string]$Region = "icrs;circle(274.7104,-13.8289,0.008)",
    [string]$Label = "epoch change M16",
    [string]$Profile = "",
    [double]$MaxMB = 1500
)
$ErrorActionPreference = "Continue"

cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\epoch-change"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") | Out-Null
$Summary = @()
$Failed = $false

function Step($Name, [scriptblock]$Body) {
    Write-Host "`n=== ${Name}: started $(Get-Date -Format HH:mm:ss) ==="
    $t = [Diagnostics.Stopwatch]::StartNew()
    $ok = $true
    try { & $Body } catch { $ok = $false; Write-Host "FAILED: $_" }
    $t.Stop()
    $line = "{0,-34} {1,-6} {2}" -f $Name, ($(if ($ok) { "ok" } else { "FAILED" })), $t.Elapsed.ToString("hh\:mm\:ss")
    Write-Host "=== $line ==="
    $script:Summary += $line
    if (-not $ok) { $script:Failed = $true }
    return $ok
}

function Sdp {
    $out = @(& uv run sdp @args 2>&1 | ForEach-Object { $s = "$_"; Write-Host $s; $s })
    if ($LASTEXITCODE -ne 0) { throw "sdp $($args[0]) $($args[1]) exited $LASTEXITCODE" }
    return ($out -join "`n")
}

function Json($Text) {
    $start = $Text.IndexOf("{"); $end = $Text.LastIndexOf("}")
    if ($start -lt 0 -or $end -lt $start) { throw "no JSON in the output" }
    return ($Text.Substring($start, $end - $start + 1) | ConvertFrom-Json)
}

Push-Location $Code
git fetch origin
git checkout $Branch
git pull origin $Branch
Set-Location (Join-Path $Code "python")
uv sync --frozen
if (-not $Profile) { $Profile = Join-Path $Code "profiles\image-changes.yaml" }

$script:PlanId = $null
$script:SetId = $null
$drafted = Step "1 Draft the plan" {
    $plan = Json (Sdp plan draft --region $Region --profile $Profile --label $Label --actor leigh)
    $script:PlanId = $plan.plan_id
    if (-not $script:PlanId) { throw "no plan_id in the draft output" }
    Sdp plan show $script:PlanId | Out-Null
    $mb = $plan.total_bytes / 1e6
    Write-Host ("  {0} items, {1:N0} MB planned" -f $plan.items, $mb)
    foreach ($g in @($plan.gaps)) { if ($g) { Write-Host "  gap: $($g.requirement_id): $($g.reason)" } }
    if (@($plan.gaps | Where-Object { $_ }).Count -gt 0) { throw "the plan has gaps; nothing fetched" }
    if ($mb -gt $MaxMB) { throw ("the plan is {0:N0} MB, over -MaxMB {1}; nothing fetched. Narrow -Region or raise -MaxMB." -f $mb, $MaxMB) }
}

if ($drafted) {
    $ran = Step "2 Approve and execute" {
        Sdp plan approve $script:PlanId --actor leigh | Out-Null
        $done = Json (Sdp plan execute $script:PlanId --actor leigh)
        $script:SetId = $done.set_id
        Write-Host ("  acquired {0}, unavailable {1}" -f @($done.execution.acquired).Count, @($done.execution.unavailable).Count)
    }
    if ($ran -and $script:SetId) {
        Step "3 Verdicts" {
            $manifest = Json (Sdp investigate show $script:SetId)
            foreach ($n in @($manifest.null_results)) {
                Write-Host ("  null fields, {0}: {1:P1} of {2} examined flagged (alpha {3})" -f $n.test_name, $n.false_positive_rate, $n.examined, $n.alpha)
            }
            $listed = Sdp investigate list $script:SetId
            $start = $listed.IndexOf("["); $end = $listed.LastIndexOf("]")
            $found = if ($start -ge 0 -and $end -gt $start) { $listed.Substring($start, $end - $start + 1) | ConvertFrom-Json } else { @() }
            # the candidates are the flagged stars; every other star measured came out steady
            $examined = $manifest.multiplicity.objects_examined
            Write-Host ("  {0} candidate(s) flagged; the rest of the {1} stars examined were steady or not on two images" -f @($found).Count, $examined)
            $shown = @{}
            foreach ($inv in @($found)) {
                $dossier = Json (Sdp investigate dossier $inv.investigation_id)
                foreach ($r in @($dossier.test_results | Where-Object { $_.test_name -eq "epoch-change" })) {
                    $d = $r.details
                    $dm = if ($null -ne $d.largest_change.delta_mag) { "{0:+0.000;-0.000} mag" -f $d.largest_change.delta_mag } else { "not seen on one image" }
                    Write-Host ("    {0,-28} {1,-9} {2,-8} {3}  p={4:E2}" -f $inv.candidate, $d.verdict, $d.band, $dm, $r.p_value)
                    foreach ($c in @($d.comparison) + @($d.per_band | ForEach-Object { $_.comparison })) {
                        if (-not $c -or $shown.ContainsKey($c.band)) { continue }
                        $shown[$c.band] = $true
                        Write-Host ("  band {0}: aperture {1:N3} arcsec, reference {2}" -f $c.band, $c.aperture_arcsec, $c.reference)
                        foreach ($i in @($c.images)) {
                            Write-Host ("    {0,-60} {1,-8} FWHM {2:N3} arcsec  zp {3:N3}  aligned on {4} stars" -f $i.image, $i.instrument, $i.fwhm_arcsec, $i.zero_point_mag, $i.alignment.matches)
                        }
                        foreach ($p in @($d.images_left_out.PSObject.Properties)) { Write-Host "    left out: $($p.Name): $($p.Value)" }
                    }
                }
            }
        } | Out-Null
    }
}

Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
if ($script:SetId) {
    Write-Host "`nEvery star's verdict, its images and their calibration are in the dossier:"
    Write-Host "  uv run sdp investigate bundle $script:SetId"
}
Stop-Transcript | Out-Null
Pop-Location
if ($Failed) { exit 1 }
