# Checks harness\lib\run-checkout.ps1 on Windows PowerShell (CI job desktop-windows, #196):
#   powershell -NoProfile -ExecutionPolicy Bypass -File harness\tests\run-checkout.tests.ps1
# Every harness script parses; a run of another branch happens in its own worktree, gets its parameters
# (switches and values with spaces too) and leaves the source checkout on its branch; a script missing
# from the branch stops with exit 1 before running anything.
$ErrorActionPreference = "Stop"
$Harness = Split-Path -Parent $PSScriptRoot
$Failures = @()
function Check([bool]$Ok, [string]$What) {
    if ($Ok) { Write-Host "ok   $What" } else { Write-Host "FAIL $What"; $script:Failures += $What }
}

foreach ($f in @(Get-ChildItem -Path $Harness -Recurse -Filter *.ps1)) {
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$errors) | Out-Null
    Check ($errors.Count -eq 0) "parses: $($f.Name) $($errors | ForEach-Object { $_.Message })"
}

$Tmp = Join-Path ([IO.Path]::GetTempPath()) ("sdp-run-checkout-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
$Src = Join-Path $Tmp "SDP"
New-Item -ItemType Directory -Force -Path (Join-Path $Src "harness\lib") | Out-Null
Copy-Item (Join-Path $Harness "lib\run-checkout.ps1") (Join-Path $Src "harness\lib\")
$Probe = @'
param([string]$Code = (Split-Path -Parent $PSScriptRoot), [string]$Branch = "main", [switch]$Flag, [string]$Word = "")
. (Join-Path $PSScriptRoot "lib\run-checkout.ps1")
Enter-RunCheckout -Code $Code -Branch $Branch -Script $PSCommandPath -Bound $PSBoundParameters
$Code = $RunCheckout.Path
@{ code = $Code; commit = $RunCheckout.Commit; flag = [bool]$Flag; word = $Word; marker = (Test-Path (Join-Path $Code "marker.txt")) } |
    ConvertTo-Json | Set-Content -LiteralPath $env:PROBE_OUT -Encoding UTF8
exit 3
'@
Set-Content -LiteralPath (Join-Path $Src "harness\run-probe.ps1") -Value $Probe -Encoding UTF8

$ErrorActionPreference = "Continue"
Push-Location $Src
git init -q -b main 2>&1 | Out-Null
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m root 2>&1 | Out-Null
git add -A; git -c user.name=t -c user.email=t@t commit -q -m probe 2>&1 | Out-Null
git checkout -q -b feature/x 2>&1 | Out-Null
Set-Content marker.txt "x"; git add marker.txt; git -c user.name=t -c user.email=t@t commit -q -m marker 2>&1 | Out-Null
git checkout -q main 2>&1 | Out-Null
git branch SDP 2>&1 | Out-Null
git checkout -q --orphan bare 2>&1 | Out-Null
git rm -rq --cached . 2>&1 | Out-Null
git -c user.name=t -c user.email=t@t commit -q --allow-empty -m empty 2>&1 | Out-Null
git checkout -q -f main 2>&1 | Out-Null
git clone -q --bare $Src (Join-Path $Tmp "origin.git") 2>&1 | Out-Null
git remote add origin (Join-Path $Tmp "origin.git")
Pop-Location

$env:PROBE_OUT = Join-Path $Tmp "probe.json"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src "harness\run-probe.ps1") -Branch feature/x -Flag -Word "two words"
$code = $LASTEXITCODE
Check ($code -eq 3) "the worktree copy's exit code comes back (got $code)"
$r = Get-Content -Raw $env:PROBE_OUT | ConvertFrom-Json
Check ($r.code -like (Join-Path "$Src-runs" "feature-x-*")) "runs in <checkout>-runs\feature-x-<hash> (got $($r.code))"
Check ([bool]$r.marker) "the worktree holds the branch's files"
Check ([bool]$r.flag) "a switch reaches the worktree copy"
Check ($r.word -eq "two words") "a value with a space reaches the worktree copy (got '$($r.word)')"
Check ((git -C $Src rev-parse --abbrev-ref HEAD) -eq "main") "the source checkout stays on main"

Remove-Item $env:PROBE_OUT
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src "harness\run-probe.ps1") -Branch bare
Check ($LASTEXITCODE -eq 1) "a branch without the script stops with exit 1"
Check (-not (Test-Path $env:PROBE_OUT)) "and nothing ran"

# a worktree root that would make the run folder the checkout itself is refused, and nothing is changed
Set-Content (Join-Path $Src "local-edit.txt") "mine"
$env:SDP_RUN_WORKTREES = $Tmp
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src "harness\run-probe.ps1") -Branch SDP
Check ($LASTEXITCODE -eq 1) "a run folder that is the checkout itself stops with exit 1"
Check ((git -C $Src rev-parse --abbrev-ref HEAD) -eq "main" -and (Test-Path (Join-Path $Src "local-edit.txt"))) "and the checkout is untouched"
# a folder in the way that is not a run worktree is left alone
New-Item -ItemType Directory -Force -Path (Join-Path $Tmp "main\.git") | Out-Null
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src "harness\run-probe.ps1") -Branch main
Check ($LASTEXITCODE -eq 1) "a folder that is not a run worktree stops with exit 1"
Remove-Item Env:\SDP_RUN_WORKTREES

# a second run of the same branch reuses the worktree after the branch moves on
Push-Location $Src
git checkout -q feature/x 2>&1 | Out-Null
Set-Content marker2.txt "y"; git add marker2.txt; git -c user.name=t -c user.email=t@t commit -q -m more 2>&1 | Out-Null
git push -q origin feature/x 2>&1 | Out-Null
$moved = git rev-parse HEAD
git checkout -q main 2>&1 | Out-Null
Pop-Location
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Src "harness\run-probe.ps1") -Branch feature/x
$r = Get-Content -Raw $env:PROBE_OUT | ConvertFrom-Json
Check ($r.commit -eq $moved) "a rerun moves the worktree to the new commit"

Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
if ($Failures.Count) { Write-Host "$($Failures.Count) failed"; exit 1 }
Write-Host "all passed"
