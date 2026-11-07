# Shared by every harness\run-*.ps1 (#196): each run happens in its own git worktree, never in your
# checkout, so a run never switches the branch you are on and a script that exists only on the branch
# being run is still found.
#
#   . (Join-Path $PSScriptRoot "lib\run-checkout.ps1")
#   Enter-RunCheckout -Code $Code -Branch $Branch -Script $PSCommandPath -Bound $PSBoundParameters
#   $Code = $RunCheckout.Path          # the worktree from here on
#   ... load the env file, Start-Transcript ...
#   Use-RunCheckout                    # logs branch and commit, points SDP_REPO_ROOT/SDP_PROFILES_DIR at the worktree
#
# The first call fetches origin/<Branch>, puts it in <Code>-runs\<branch> (made on first use, moved to the
# fetched commit after that; local edits there are discarded, untracked files such as python\.venv kept),
# and runs that worktree's copy of the script with the same parameters, then exits with its exit code.
# The copy it runs sees SDP_RUN_CHECKOUT and carries on. Set SDP_RUN_WORKTREES to keep the worktrees
# somewhere else. Two runs of the same branch at once share one worktree; different branches never do.

# Native output (sdp, uv, git) is UTF-8: read it as such so the log shows ± and not Â± (#201).
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$script:RunCheckout = $null

function Get-RunWorktreePath([string]$Code, [string]$Branch) {
    $root = if ($env:SDP_RUN_WORKTREES) { $env:SDP_RUN_WORKTREES } else { "$($Code.TrimEnd('\', '/'))-runs" }
    $safe = $Branch -replace '[\\/:*?"<>| ]', '-'
    if ($safe -ne $Branch) {
        # feature/x and feature-x must not share a folder: a short hash of the real name keeps them apart
        $sha = [System.Security.Cryptography.SHA1]::Create()
        $hash = -join ($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Branch))[0..2] | ForEach-Object { $_.ToString("x2") })
        $safe = "$safe-$hash"
    }
    return (Join-Path $root $safe)
}

# Whether $Tree is a linked worktree of $Code's repository (never the main checkout, whose git dir is
# the common one): only such a folder is ever force-checked-out, so a misconfigured SDP_RUN_WORKTREES
# can never point the run at your own checkout. git answers both paths, so they compare alike.
function Test-RunWorktree([string]$Code, [string]$Tree) {
    if (-not (Test-Path -LiteralPath (Join-Path $Tree ".git") -PathType Leaf)) { return $false }
    $common = git -C $Code rev-parse --path-format=absolute --git-common-dir 2>$null
    $theirs = git -C $Tree rev-parse --path-format=absolute --git-common-dir 2>$null
    $own = git -C $Tree rev-parse --path-format=absolute --git-dir 2>$null
    return ($common -and $theirs -and ($theirs -ieq $common) -and ($own -ine $common))
}

function Get-RunArguments($Bound, [string[]]$Drop) {
    $out = @()
    foreach ($k in $Bound.Keys) {
        if ($Drop -contains $k) { continue }
        $v = $Bound[$k]
        if ($v -is [System.Management.Automation.SwitchParameter]) {
            if ($v.IsPresent) { $out += "-$k" }
        } else {
            $out += "-$k"
            $out += "$v"
        }
    }
    return , $out
}

function Stop-Run([string]$Message) {
    Write-Host "STOP: $Message"
    exit 1
}

function Enter-RunCheckout {
    param(
        [Parameter(Mandatory)] [string]$Code,
        [Parameter(Mandatory)] [string]$Branch,
        [Parameter(Mandatory)] [string]$Script,
        $Bound = @{},
        # parameters the worktree's copy must not be given again (a SecureString does not survive the command line)
        [string[]]$Drop = @()
    )
    $here = (Resolve-Path -LiteralPath (Split-Path -Parent (Split-Path -Parent $Script))).Path
    if ($env:SDP_RUN_CHECKOUT -and ((Resolve-Path -LiteralPath $env:SDP_RUN_CHECKOUT).Path -eq $here)) {
        # the worktree's own copy, started below: carry on here
        $script:RunCheckout = [pscustomobject]@{
            Path   = $here
            Branch = $Branch
            Source = $env:SDP_RUN_SOURCE
            Commit = (git -C $here rev-parse HEAD)
        }
        return
    }

    $Code = (Resolve-Path -LiteralPath $Code).Path
    git -C $Code fetch origin $Branch
    if ($LASTEXITCODE -ne 0) { Stop-Run "git fetch origin $Branch failed in $Code (is the branch name right?)" }
    $commit = git -C $Code rev-parse "origin/$Branch"
    if ($LASTEXITCODE -ne 0) { Stop-Run "no origin/$Branch in $Code" }

    $tree = Get-RunWorktreePath $Code $Branch
    if ([System.IO.Path]::GetFullPath($tree).TrimEnd('\', '/') -ieq $Code.TrimEnd('\', '/')) {
        Stop-Run "the run worktree for $Branch would be your checkout $Code; set SDP_RUN_WORKTREES to another folder"
    }
    if (Test-RunWorktree $Code $tree) {
        git -C $tree checkout --quiet --force --detach $commit
        if ($LASTEXITCODE -ne 0) { Stop-Run "could not move the run worktree $tree to $commit" }
    } elseif (Test-Path -LiteralPath $tree) {
        Stop-Run "$tree exists but is not a run worktree of $Code, so nothing there is touched; move it or set SDP_RUN_WORKTREES"
    } else {
        git -C $Code worktree prune
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $tree) | Out-Null
        git -C $Code worktree add --detach $tree $commit
        if ($LASTEXITCODE -ne 0) { Stop-Run "git worktree add $tree failed" }
    }

    $name = Split-Path -Leaf $Script
    $target = Join-Path $tree "harness\$name"
    if (-not (Test-Path -LiteralPath $target)) {
        Stop-Run "harness\$name is not on $Branch (commit $($commit.Substring(0, 7))); check -Branch"
    }
    Write-Host "Running harness\$name from $Branch at $($commit.Substring(0, 7)) in $tree (your checkout $Code is not touched)"
    $again = Get-RunArguments $Bound ($Drop + @("Code"))
    $env:SDP_RUN_CHECKOUT = $tree
    $env:SDP_RUN_SOURCE = $Code
    $shell = (Get-Process -Id $PID).Path
    & $shell -NoProfile -ExecutionPolicy Bypass -File $target -Code $tree @again
    exit $LASTEXITCODE
}

# After the env file is loaded and the transcript started: say what runs, and make sdp use the worktree.
function Use-RunCheckout {
    if (-not $script:RunCheckout) { return }
    $r = $script:RunCheckout
    $subject = git -C $r.Path log -1 --format=%s
    Write-Host "Branch $($r.Branch) at $($r.Commit) ($subject)"
    Write-Host "Worktree $($r.Path)"
    # an env file written for the ordinary checkout points these at it; the code that runs is here
    $env:SDP_REPO_ROOT = $r.Path
    if (-not $env:SDP_PROFILES_DIR -or ($r.Source -and $env:SDP_PROFILES_DIR.StartsWith($r.Source.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase))) {
        $env:SDP_PROFILES_DIR = Join-Path $r.Path "profiles"
    }
}
