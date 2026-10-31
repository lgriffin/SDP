# SDP idle-PC run: four long jobs one after another, everything logged (see harness/README.md).
#
#   powershell -ExecutionPolicy Bypass -File harness\run-idle-pc.ps1
#   powershell -ExecutionPolicy Bypass -File harness\run-idle-pc.ps1 -EnvFile E:\SDP\sdp-env.cmd
#
# 1. Praesepe 3x3 with the fast clusterer (#153); if it finishes in under 15 minutes, the 4x4 repeat
# 2. Aquila stage 2 rerun (#147)
# 3. Grade your frames, then light curves from your subs
# None of it touches <SDP data>\sweep. A job that fails is logged and the next one runs.

param(
    # the checkout this script sits in, unless given
    [string]$Code = (Split-Path -Parent $PSScriptRoot),
    # the settings the SDP Command Prompt loads (install-dev-tools guide)
    [string]$EnvFile = "D:\SDP\sdp-env.cmd"
)
$ErrorActionPreference = "Continue"

# The SDP Command Prompt's settings (database, data folder) into this PowerShell session
cmd /c "call `"$EnvFile`" >nul && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}
$Data = $env:SDP_DATA
if (-not $Data) { Write-Error "SDP_DATA is not set by $EnvFile"; exit 1 }

$Reports = Join-Path $Data "reports\idle-pc"
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
    return $t.Elapsed
}

function Sdp {
    # each line is shown as it comes and also kept, so a long run is not silent
    $out = @(& uv run sdp @args 2>&1 | ForEach-Object { $s = "$_"; Write-Host $s; $s })
    if ($LASTEXITCODE -ne 0) { throw "sdp $($args[0]) $($args[1]) exited $LASTEXITCODE" }
    return ($out -join "`n")
}

function Investigate($Region, $ProfilePath, $Label) {
    $draft = Sdp plan draft --region $Region --profile $ProfilePath --label $Label --actor leigh
    $start = $draft.IndexOf("{"); $end = $draft.LastIndexOf("}")
    if ($start -lt 0 -or $end -lt $start) { throw "no JSON in the draft output" }
    $json = $draft.Substring($start, $end - $start + 1)
    $id = ($json | ConvertFrom-Json).plan_id
    if (-not $id) { throw "no plan_id in the draft output" }
    Sdp plan approve $id --actor leigh | Out-Null
    Sdp plan execute $id --actor leigh | Out-Null
}

# Update the code
Push-Location $Code
git checkout main
git pull
Set-Location (Join-Path $Code "python")
uv sync --frozen

# The fast copy of stress-groups.yaml: new profile_id, and clusterer: fast-hdbscan in astrometric-coherence
$Profiles = Join-Path $Data "profiles"
$Groups = Join-Path $Profiles "stress-groups.yaml"
$Fast = Join-Path $Profiles "stress-groups-fast.yaml"
if (-not (Test-Path $Fast)) {
    $text = Get-Content -Raw $Groups
    $text = $text -replace "(?m)^profile_id:.*$", "profile_id: stress-groups-fast"
    $text = [regex]::Replace($text, "(?s)(name: astrometric-coherence.*?thresholds:\s*\r?\n)(\s+)(source_id: gaia)",
        { param($m) $m.Groups[1].Value + $m.Groups[2].Value + $m.Groups[3].Value + "`r`n" + $m.Groups[2].Value + "clusterer: fast-hdbscan" })
    if ($text -notmatch "clusterer: fast-hdbscan") { Write-Error "could not add the clusterer line to $Fast"; exit 1 }
    Set-Content -Path $Fast -Value $text -NoNewline
    Write-Host "Wrote $Fast"
}

$praesepe = @(Step "1 Praesepe 3x3 fast" { Investigate "icrs;box(130.1,19.67,3,3)" $Fast "Stress 3 Praesepe fast" })[-1]
if ($praesepe.TotalMinutes -lt 15) {
    Step "1b Praesepe 4x4 fast" { Investigate "icrs;box(130.1,19.67,4,4)" $Fast "Stress 3 Praesepe 4x4 fast" } | Out-Null
} else { Write-Host "Praesepe 3x3 took over 15 minutes, so the 4x4 repeat is skipped" }

Step "2 Aquila stage 2 rerun" {
    Investigate "icrs;box(280,0,9.5,9.5)" (Join-Path $Profiles "stress-huge.yaml") "Stress 2 Aquila rerun"
} | Out-Null

Step "3a Grade frames" { Sdp local quality astro | Out-Null } | Out-Null
Step "3b Light curves" { Sdp local lightcurve astro | Out-Null } | Out-Null

Pop-Location
Write-Host "`n=== Summary ==="
$Summary | ForEach-Object { Write-Host $_ }
Stop-Transcript | Out-Null
Write-Host "Log: $Reports\run-$Stamp.log"
