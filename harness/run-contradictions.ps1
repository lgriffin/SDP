# SDP cross-survey contradictions on two real fields (INV-026 to INV-029), everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-contradictions.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-contradictions.ps1 -Branch claude/project-thread-1pvnyf
#   powershell -ExecutionPolicy Bypass -File harness\run-contradictions.ps1 -SkipImages
#
# 1. Updates the checkout (-Branch, main by default) and registers the archive descriptors; stops if either fails
# 2. Catalogues: drafts, approves and executes profiles\survey-contradictions.yaml on -CatalogueRegion
#    (M67 by default: an old cluster with thousands of Gaia stars that 2MASS, Pan-STARRS and AllWISE all cover)
# 3. Images: the same for profiles\image-contradictions.yaml on -ImageRegion (a 29-arcsecond patch of the
#    Pillars of Creation, WFC3 2014 and NIRCam 2022), stopping before any fetch if the plan is over -MaxMB
# 4. For each run: the null-field rate, how many stars were judged, each candidate's pattern and every
#    comparison that disagreed (survey, difference, z), and the images left out and why
#
# Nothing is deleted; results land in SDP's database like any other investigation, and each run's
# dossier (sdp investigate bundle) carries the same comparisons in its README.

param(
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    [string]$EnvFile = "D:\SDP\sdp-env.cmd",
    [string]$Branch = "main",
    [string]$CatalogueRegion = "icrs;circle(132.846,11.814,0.1)",
    [string]$ImageRegion = "icrs;circle(274.7104,-13.8289,0.008)",
    [double]$MaxMB = 1500,
    [switch]$SkipImages
)
$ErrorActionPreference = "Continue"

cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\contradictions"
New-Item -ItemType Directory -Force -Path $Reports | Out-Null
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
Start-Transcript -Path (Join-Path $Reports "run-$Stamp.log") | Out-Null
$Summary = @()
$Failed = $false
$Sets = @()

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

function Difference($c) {
    if ($c.kind -eq "band") {
        $word = if ($c.residual_mag -lt 0) { "brighter" } else { "fainter" }
        return ("{0:N2} mag {1} than Gaia predicts" -f [math]::Abs($c.residual_mag), $word)
    }
    $east = $c.offset_mas[0] - $c.field_offset_mas[0]; $north = $c.offset_mas[1] - $c.field_offset_mas[1]
    return ("{0:N0} mas out of place ({1:+0;-0} east, {2:+0;-0} north)" -f [math]::Sqrt($east * $east + $north * $north), $east, $north)
}

# Draft, check, approve and execute one profile on one region; prints what it found. Returns the set id.
function RunProfile($Label, $Profile, $Region) {
    $plan = Json (Sdp plan draft --region $Region --profile $Profile --label $Label --actor leigh)
    if (-not $plan.plan_id) { throw "no plan_id in the draft output" }
    $mb = $plan.total_bytes / 1e6
    Write-Host ("  {0} items, {1:N0} MB planned" -f $plan.items, $mb)
    foreach ($g in @($plan.gaps)) { if ($g) { Write-Host "  gap: $($g.requirement_id): $($g.reason)" } }
    if (@($plan.gaps | Where-Object { $_ }).Count -gt 0) { throw "the plan has gaps; nothing fetched" }
    if ($mb -gt $MaxMB) { throw ("the plan is {0:N0} MB, over -MaxMB {1}; nothing fetched" -f $mb, $MaxMB) }
    Sdp plan approve $plan.plan_id --actor leigh | Out-Null
    $done = Json (Sdp plan execute $plan.plan_id --actor leigh)
    $set = $done.set_id
    if (-not $set) { throw "no set_id from the execution" }
    $manifest = Json (Sdp investigate show $set)
    foreach ($n in @($manifest.null_results)) {
        Write-Host ("  null fields, {0}: {1:P1} of {2} examined flagged (alpha {3})" -f $n.test_name, $n.false_positive_rate, $n.examined, $n.alpha)
    }
    $listed = Sdp investigate list $set
    $start = $listed.IndexOf("["); $end = $listed.LastIndexOf("]")
    $found = if ($start -ge 0 -and $end -gt $start) { $listed.Substring($start, $end - $start + 1) | ConvertFrom-Json } else { @() }
    Write-Host ("  {0} candidate(s) among {1} stars examined" -f @($found).Count, $manifest.multiplicity.objects_examined)
    $leftOut = $false
    foreach ($inv in @($found)) {
        $dossier = Json (Sdp investigate dossier $inv.investigation_id)
        foreach ($r in @($dossier.test_results | Where-Object { $_.test_name -eq "cross-survey-contradiction" })) {
            $d = $r.details
            Write-Host ("    {0,-30} {1,-24} p={2:E2}  {3} counted, {4} set aside" -f $inv.candidate, $d.pattern, $r.p_value, $d.comparisons_counted, $d.comparisons_set_aside)
            foreach ($c in @($d.comparisons | Where-Object { $d.flagged -contains $_.channel })) {
                Write-Host ("      {0,-48} {1}  z={2:+0.0;-0.0}" -f $c.channel, (Difference $c), $c.z)
            }
            if (-not $leftOut -and $d.images_left_out) {
                foreach ($p in @($d.images_left_out.PSObject.Properties)) { Write-Host "    image left out: $($p.Name): $($p.Value)" }
                $leftOut = $true
            }
        }
    }
    return $set
}

function Native([string]$What, [scriptblock]$Body) {
    & $Body | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$What exited $LASTEXITCODE" }
}

Push-Location $Code
# nothing runs on a checkout or an environment that failed to update
$ready = Step "0 Update checkout and tools" {
    Native "git fetch" { git fetch origin }
    Native "git checkout" { git checkout $script:Branch }
    Native "git pull" { git pull origin $script:Branch }
    Set-Location (Join-Path $script:Code "python")
    Native "uv sync" { uv sync --frozen }
}
# the planner matches each requirement against the registered archive descriptors
$ready = $ready -and (Step "0 Register descriptors" { Sdp integration register | Out-Null })

if ($ready) {
    Step "1 Catalogues (M67)" {
        $script:Sets += RunProfile "survey contradictions M67" (Join-Path $Code "profiles\survey-contradictions.yaml") $CatalogueRegion
    } | Out-Null

    if (-not $SkipImages) {
        Step "2 Images (M16)" {
            $script:Sets += RunProfile "image contradictions M16" (Join-Path $Code "profiles\image-contradictions.yaml") $ImageRegion
        } | Out-Null
    }
}

Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
foreach ($s in @($Sets | Where-Object { $_ })) {
    Write-Host "`nEvery comparison, with its budget, is in the candidate pages and the dossier:"
    Write-Host "  uv run sdp investigate bundle $s"
}
Stop-Transcript | Out-Null
Pop-Location
if ($Failed) { exit 1 }
