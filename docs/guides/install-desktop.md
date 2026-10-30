# Install the Windows desktop app

**Time:** about 10 minutes · **Needs:** Windows 10 or 11 (64-bit), about 2 GB of disk for the app, a
GitHub account to download the installer, and an internet connection for every run

The desktop app is SDP in one window. It brings its own database and its own copy of Python, so there
is nothing else to install and you do not need administrator rights. It also adds the
**SDP Command Prompt** to the Start menu, for the few steps that are typed rather than clicked.

## 1. Download the installer

Releases will carry the installer as a file called `SDP-Setup-<version>.exe`. The current release,
v0.2.0, does not have one attached yet, so take it from the latest automatic build instead:

1. Sign in to GitHub and open the repository's **Actions** tab:
   <https://github.com/lgriffin/SDP/actions/workflows/ci.yml?query=branch%3Amain>
2. Click the most recent run with a green tick.
3. Scroll to **Artifacts** at the foot of the page and click **sdp-desktop-windows**. A zip file downloads.
   GitHub keeps these for 14 days, so take it from a recent run.
4. Open the zip and drag `SDP-Setup-<version>.exe` out to your Downloads folder.

Once a release has the installer attached, take it from the release page instead:
<https://github.com/lgriffin/SDP/releases>.

## 2. Install it

1. Double-click `SDP-Setup-<version>.exe`.
2. **Windows protected your PC** appears, because the installer is not yet signed with a
   code-signing certificate. This is expected. Click **More info**, check the app name says `SDP`, then
   click **Run anyway**.
3. It installs in a few seconds, for you only, into `%LOCALAPPDATA%\Programs\SDP`, and opens the app.

## 3. Optional, but do it now: choose where SDP keeps its data

By default SDP keeps everything (its database, every downloaded file, its records and logs) in
`%LOCALAPPDATA%\SDP` on your C: drive. The journeys that copy all of Gaia need hundreds of gigabytes,
so if you have a bigger drive, point SDP there **before** the first real run:

1. Close the SDP app.
2. Open an ordinary Command Prompt (press the Windows key, type `cmd`, press Enter) and type:
   ```bat
   setx SDP_DESKTOP_DATA D:\SDP
   ```
   It answers `SUCCESS: Specified value was saved.`
3. Open SDP again from the Start menu. On this first start it creates `D:\SDP` with a fresh database
   and copies the default test profiles to `D:\SDP\profiles`.

Anything already in `%LOCALAPPDATA%\SDP` stays there unused. You can delete it once you are happy.

## What success looks like

- The SDP window opens on the **home page** within a minute. The first start takes longest, because it
  creates the database.
- The Start menu has **SDP** and **SDP Command Prompt**.
- Open **SDP Command Prompt** and type:
  ```bat
  sdp --help
  ```
  It lists the commands: `investigate`, `plan`, `archive`, `demo` and the rest. Inside this prompt,
  `%SDP_DATA%` is SDP's data folder (`D:\SDP` if you followed step 3).

The Command Prompt only works while the app is open, because the app runs the database. You can
minimise the app and never click in it.

## One more step before planning anything

A new database does not yet know what each archive offers, so it cannot plan downloads. Do this
once, on the **Start** page: click **Read what the archives offer**. It takes a few minutes, because
MAST's description comes from the Virtual Observatory registry. Or, in the SDP Command Prompt:

```bat
sdp integration register
```

**Success:** the Start page lists each archive with the time its description was read; the command
prints a JSON list of registered sources.

## Where things are

| What | Where |
|---|---|
| The app | `%LOCALAPPDATA%\Programs\SDP` |
| Your data | `%LOCALAPPDATA%\SDP`, or the folder `SDP_DESKTOP_DATA` names |
| Downloaded and made files | `<data>\artifacts` |
| Test profiles you can edit | `<data>\profiles` |
| Logs | `<data>\logs` |

Uninstalling (Windows Settings, Apps, SDP) removes the app but keeps your data.

## If it goes wrong

- **The app shows an error on start.** It offers to open the `logs` folder. The newest file says why.
  Attach it to a GitHub issue.
- **"Open the SDP app first, then open this prompt again."** The SDP Command Prompt needs the app
  running. Open the app, wait for the home page, then open the prompt again.
- **SmartScreen gives no "Run anyway" button.** Your organisation may block unsigned programs. Use the
  [developer tools](install-dev-tools.md) route instead.
- **Signed steps.** The app records runs without a signature, which is fine for exploring. A step that
  needs your signature (approving a plan over 500 MB, freezing a predeclaration) is shown as a command
  to paste into the SDP Command Prompt, after `set SDP_SIGNING_KEY=C:\path\to\key` and
  `set SDP_SIGNER=your-name`. The [developer tools guide](install-dev-tools.md#a-signing-key-only-for-signed-steps)
  shows how to make a key.

How the app is built, and what runs inside it, is in
[typescript/apps/sdp-desktop/README.md](../../typescript/apps/sdp-desktop/README.md).

Next: [your first look at a field](first-look.md).
