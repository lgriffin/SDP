<#
.SYNOPSIS
    One-step developer setup for SDP on Windows: tools, checkout, Python and Node packages, the UI build,
    and an "SDP (dev)" shortcut that opens the desktop app straight from the checkout.

.DESCRIPTION
    Run it once on a new PC, and again whenever you like: each run brings everything up to date and skips
    what is already done. It needs no administrator rights except where winget asks for them while
    installing Git or Node.js.

    What it does:
      1. installs Git, uv and Node.js (LTS) with winget if they are missing;
      2. clones the repository, or with -Update pulls the current branch (only when nothing is uncommitted);
      3. uv sync --frozen (Python and every SDP package, in python\.venv);
      4. pnpm install, Electron, the UI build and the desktop app;
      5. writes desktop-dev.json in the data folder (importing an existing sdp-env.cmd, if you have one);
      6. optionally makes a signing key (-Signer);
      7. adds "SDP (dev)" and "SDP dev prompt" to the Start menu and "SDP (dev)" to the desktop.

    "SDP (dev)" then starts PostgreSQL, runs the migrations, registers the archives on the first start,
    starts sdp-api and shows the app. Its Developer menu opens a prompt where `sdp` works directly, and
    "Update and restart" runs this script again with -Update.

.EXAMPLE
    # A new PC, from an empty PowerShell window:
    irm https://raw.githubusercontent.com/lgriffin/SDP/main/setup-dev.ps1 -OutFile $env:TEMP\setup-dev.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\setup-dev.ps1 -Checkout D:\code\SDP -Data D:\SDP -Launch

.EXAMPLE
    # An existing checkout and data folder, keeping the database the old sdp-env.cmd points at:
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\code\SDP\setup-dev.ps1 -Data D:\SDP -ImportEnv D:\SDP\sdp-env.cmd -Launch
#>
[CmdletBinding()]
param(
    # The repository checkout. Default: the checkout this script sits in, else %USERPROFILE%\code\SDP.
    [string]$Checkout,
    # SDP's data folder: database, artifacts, ledger, logs. Keep it outside the checkout.
    [string]$Data = (Join-Path $env:LOCALAPPDATA "SDP-dev"),
    [string]$Repository = "https://github.com/lgriffin/SDP.git",
    # An sdp-env.cmd from the manual setup; its settings (database address included) go into desktop-dev.json.
    [string]$ImportEnv,
    # Make an Ed25519 signing key for this name (for signed steps) if there is none yet.
    [string]$Signer,
    # Pull the checkout's current branch before installing.
    [switch]$Update,
    # Open SDP (dev) at the end.
    [switch]$Launch,
    [switch]$NoShortcuts,
    # Used by the app's "Update and restart": wait for that process to close first.
    [int]$WaitForPid = 0
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONUTF8 = "1"
# corepack would otherwise stop to ask before fetching the pinned pnpm.
$env:COREPACK_ENABLE_DOWNLOAD_PROMPT = "0"

$script:step = 0
function Step([string]$Text) {
    $script:step++
    Write-Host ""
    Write-Host ("[{0}/8] {1}" -f $script:step, $Text) -ForegroundColor Cyan
}

function Test-Command([string]$Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

# Programs installed a moment ago are on the saved PATH, not yet on this window's.
function Update-SessionPath {
    $parts = @([Environment]::GetEnvironmentVariable("Path", "Machine"), [Environment]::GetEnvironmentVariable("Path", "User"), $env:Path)
    $seen = @{}
    $kept = New-Object System.Collections.Generic.List[string]
    foreach ($dir in (($parts -join ";") -split ";")) {
        if ($dir -and -not $seen.ContainsKey($dir.ToLower())) { $seen[$dir.ToLower()] = $true; $kept.Add($dir) }
    }
    $env:Path = $kept -join ";"
    $uvBin = Join-Path $HOME ".local\bin"
    if ((Test-Path $uvBin) -and ($env:Path -notlike "*$uvBin*")) { $env:Path = "$uvBin;$env:Path" }
}

# Run a native command and stop with its name when it fails.
function Invoke-Checked([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE). The lines above say why." }
}

function Install-Tool([string]$Command, [string]$WingetId, [string]$Name, [scriptblock]$Fallback) {
    if (Test-Command $Command) { Write-Host "  $Name found"; return }
    Write-Host "  installing $Name"
    if (Test-Command "winget") {
        winget install --id $WingetId --exact --silent --accept-source-agreements --accept-package-agreements | Out-Host
    } elseif ($Fallback) {
        & $Fallback
    } else {
        throw "$Name is missing and winget is not available. Install $Name yourself, then run this script again."
    }
    Update-SessionPath
    if (-not (Test-Command $Command)) {
        throw "$Name was installed but this window cannot find it yet. Close PowerShell, open a new one and run this script again."
    }
}

function Invoke-Pnpm {
    if (Test-Command "pnpm") { & pnpm @args } else { & corepack pnpm @args }
}

# ---------------------------------------------------------------------------------------------------
if (-not $Checkout) {
    $Checkout = if (Test-Path (Join-Path $PSScriptRoot "python\pyproject.toml")) { $PSScriptRoot } else { Join-Path $HOME "code\SDP" }
}
$Checkout = [IO.Path]::GetFullPath($Checkout)
$Data = [IO.Path]::GetFullPath($Data)
if ($Data.TrimEnd("\").StartsWith($Checkout.TrimEnd("\") + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "The data folder ($Data) must not be inside the checkout ($Checkout)."
}
$appDir = Join-Path $Checkout "typescript\apps\sdp-desktop"
$electron = Join-Path $appDir "node_modules\electron\dist\electron.exe"

Write-Host "SDP developer setup" -ForegroundColor Green
Write-Host "  checkout:    $Checkout"
Write-Host "  data folder: $Data"

if ($WaitForPid -gt 0) {
    Write-Host "  waiting for SDP (dev) to close"
    Wait-Process -Id $WaitForPid -Timeout 120 -ErrorAction SilentlyContinue
}
# A running Electron from this checkout locks its own files, and pnpm install would fail half-way.
$running = Get-Process -Name electron -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Checkout, [StringComparison]::OrdinalIgnoreCase) }
if ($running) { throw "SDP (dev) is open. Close it, then run this script again." }

# ---------------------------------------------------------------------------------------------------
Step "Tools: Git, uv and Node.js"
Update-SessionPath
Install-Tool "git" "Git.Git" "Git" $null
Install-Tool "uv" "astral-sh.uv" "uv" { powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex" }
Install-Tool "node" "OpenJS.NodeJS.LTS" "Node.js" $null
$nodeMajor = [int]((node --version) -replace '^v(\d+).*', '$1')
if ($nodeMajor -lt 22) {
    throw "Node.js $(node --version) is too old; SDP needs 22 or newer. Run: winget upgrade --id OpenJS.NodeJS.LTS"
}

# ---------------------------------------------------------------------------------------------------
Step "The code"
if (-not (Test-Path (Join-Path $Checkout ".git"))) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Checkout) | Out-Null
    Invoke-Checked "git clone" { git clone $Repository $Checkout }
} elseif ($Update) {
    $dirty = git -C $Checkout status --porcelain --untracked-files=no
    if ($dirty) {
        Write-Warning "The checkout has uncommitted changes, so it was not pulled. Commit or stash them and update again."
    } else {
        git -C $Checkout pull --ff-only
        if ($LASTEXITCODE -ne 0) { Write-Warning "git pull did not complete (no upstream, or the branch has diverged); carrying on with the code as it is." }
    }
}
$branch = git -C $Checkout rev-parse --abbrev-ref HEAD
Write-Host "  on branch $branch at $(git -C $Checkout rev-parse --short HEAD)"
# Checkouts made before .gitattributes (#193) may hold .sh files with Windows line endings.
Push-Location $Checkout
try {
    foreach ($file in (git ls-files "*.sh")) {
        if ((Test-Path $file) -and ([IO.File]::ReadAllText((Join-Path $Checkout $file)).Contains("`r"))) {
            Remove-Item $file
            git checkout -- $file
            Write-Host "  restored line endings of $file"
        }
    }
} finally { Pop-Location }

# ---------------------------------------------------------------------------------------------------
Step "Python and the SDP packages (uv sync, a minute or two)"
Push-Location (Join-Path $Checkout "python")
try { Invoke-Checked "uv sync" { uv sync --frozen } } finally { Pop-Location }

# ---------------------------------------------------------------------------------------------------
Step "Node packages, Electron, the UI build and the desktop app"
Push-Location $Checkout
try {
    Invoke-Checked "pnpm install" { Invoke-Pnpm install --frozen-lockfile }
    # pnpm leaves Electron's own download to us (see pnpm-workspace.yaml).
    Invoke-Checked "the Electron download" { node (Join-Path $appDir "node_modules\electron\install.js") }
    Invoke-Checked "the UI build" { Invoke-Pnpm --filter "@sdp/evidence-ui" build }
    Invoke-Checked "the desktop app build" { Invoke-Pnpm --filter "@sdp/desktop" compile }
} finally { Pop-Location }

# ---------------------------------------------------------------------------------------------------
Step "Settings (desktop-dev.json)"
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$configPath = Join-Path $Data "desktop-dev.json"
$settings = [ordered]@{}
if (Test-Path $configPath) {
    $existing = Get-Content -Raw -Path $configPath | ConvertFrom-Json
    if ($existing.env) { foreach ($p in $existing.env.PSObject.Properties) { $settings[$p.Name] = [string]$p.Value } }
    Write-Host "  kept $configPath"
} elseif (-not $ImportEnv -and (Test-Path (Join-Path $Data "sdp-env.cmd"))) {
    $ImportEnv = Join-Path $Data "sdp-env.cmd"
}
if ($ImportEnv) {
    if (-not (Test-Path $ImportEnv)) { throw "No file at $ImportEnv to import." }
    # The app sets these from the checkout and data folder itself.
    $skip = @("SDP_REPO_ROOT", "SDP_PROFILES_DIR", "PATH", "PYTHONUTF8")
    foreach ($line in Get-Content -Path $ImportEnv) {
        if ($line -match '^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$') {
            $name = $Matches[1]
            if ($skip -contains $name.ToUpper()) { continue }
            $settings[$name] = [Environment]::ExpandEnvironmentVariables($Matches[2])
        }
    }
    Write-Host "  imported the settings in $ImportEnv"
}

# ---------------------------------------------------------------------------------------------------
Step "Signing key"
if ($Signer -and -not $settings.Contains("SDP_SIGNING_KEY")) {
    $keyDir = Join-Path $Data "keys"
    $key = Join-Path $keyDir "sdp_ledger"
    New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
    if (-not (Test-Path $key)) {
        # SDP loads the key without a passphrase. One argument string passes -N "" the same way on every PowerShell.
        $keygen = Start-Process -FilePath "ssh-keygen" -ArgumentList "-q -t ed25519 -N `"`" -C $Signer -f `"$key`"" -NoNewWindow -Wait -PassThru
        if ($keygen.ExitCode -ne 0) { throw "ssh-keygen failed (exit code $($keygen.ExitCode))." }
        Write-Host "  made $key (keep it private: anyone with it can sign as you)"
    }
    $ledger = if ($settings.Contains("SDP_LEDGER_DIR")) { $settings["SDP_LEDGER_DIR"] } else { Join-Path $Data "ledger" }
    New-Item -ItemType Directory -Force -Path $ledger | Out-Null
    $allowed = Join-Path $ledger "allowed_signers"
    $entry = "$Signer $((Get-Content -Raw -Path "$key.pub").Trim())"
    if (-not ((Test-Path $allowed) -and ((Get-Content -Path $allowed) -contains $entry))) {
        Add-Content -Path $allowed -Value $entry -Encoding Ascii
        Write-Host "  added $Signer to $allowed"
    }
    $settings["SDP_SIGNING_KEY"] = $key
    $settings["SDP_SIGNER"] = $Signer
} elseif ($settings.Contains("SDP_SIGNING_KEY")) {
    Write-Host "  using $($settings["SDP_SIGNING_KEY"])"
} else {
    Write-Host "  none (exploring never needs one; run again with -Signer <your-name> for signed steps)"
}
@{ env = $settings } | ConvertTo-Json -Depth 4 | Set-Content -Path $configPath -Encoding UTF8
Write-Host "  wrote $configPath"
if ($settings.Contains("SDP_DATABASE_URL")) {
    Write-Host "  database: the one in SDP_DATABASE_URL (started with sdp db start when it is local)"
} else {
    Write-Host "  database: SDP (dev)'s own, made in $Data\postgres on first start"
}

# ---------------------------------------------------------------------------------------------------
Step "Shortcuts"
$appArgs = "`"$appDir`" --sdp-dev `"--sdp-data=$Data`""
if ($NoShortcuts) {
    Write-Host "  skipped"
} else {
    $shell = New-Object -ComObject WScript.Shell
    $programs = [Environment]::GetFolderPath("Programs")
    $links = @(
        @{ Path = Join-Path $programs "SDP (dev).lnk"; Target = $electron; Args = $appArgs; Dir = $appDir; Note = "SDP run from $Checkout" },
        @{ Path = Join-Path ([Environment]::GetFolderPath("Desktop")) "SDP (dev).lnk"; Target = $electron; Args = $appArgs; Dir = $appDir; Note = "SDP run from $Checkout" },
        @{ Path = Join-Path $programs "SDP dev prompt.lnk"; Target = "$env:SystemRoot\System32\cmd.exe"; Args = "/k `"$Data\runtime\sdp-dev-prompt.cmd`""; Dir = $Checkout; Note = "sdp commands on $Checkout (open SDP (dev) once first)" }
    )
    foreach ($l in $links) {
        $lnk = $shell.CreateShortcut($l.Path)
        $lnk.TargetPath = $l.Target
        $lnk.Arguments = $l.Args
        $lnk.WorkingDirectory = $l.Dir
        $lnk.Description = $l.Note
        $lnk.Save()
        Write-Host "  $($l.Path)"
    }
}

# ---------------------------------------------------------------------------------------------------
Step "Done"
Write-Host "  Open 'SDP (dev)' from the Start menu or desktop. The first start makes the database and registers"
Write-Host "  the archives (about 3 minutes); later starts take seconds. Developer > Open the developer prompt gives"
Write-Host "  a console where 'sdp ...' runs this checkout's code against the running app."
if ($Launch) {
    Start-Process -FilePath $electron -ArgumentList $appArgs -WorkingDirectory $appDir
    Write-Host "  SDP (dev) is starting."
}
