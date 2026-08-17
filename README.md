# Claude Usage Widget

> **Unofficial community project.** Not affiliated with, endorsed by, or
> supported by Anthropic. "Claude" is Anthropic's trademark; this project only
> uses the name to describe what it displays. It reads an undocumented endpoint
> that can change or stop working without notice.

An always-on-top desktop widget for Windows 11 that mirrors Claude Code's
`/usage` panel: your 5-hour limit, weekly limits, and usage credits, refreshed
every 5 minutes.

The bar fill is colour-coded — blue below 70%, **yellow at 70%**, **red at 85%** —
and a Windows toast fires the first time a limit crosses 70% and 80%. The panel
itself stays dark; only the bars change colour.

```
┌──────────────────────────────────────────────┐
│ Plan usage limits · Max (20x)              → │
│ 5-hour limit        Resets in 4 hr 44 min  4%│
│ ▓▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁ │
│ Weekly · all models  Resets Fri 5:00 PM  20% │
│ ▓▓▓▓▓▓▓▓▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁ │
│ …                                            │
└──────────────────────────────────────────────┘
```

## Requirements

- Windows 11 (Windows 10 works too)
- Python 3.10+
- Claude Code installed and signed in — run `claude` once and `/login` if you
  have not already

## Run it

```powershell
git clone https://github.com/ylanjewar/claude_windows_widget.git
cd claude_windows_widget
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m claude_usage_widget
```

That last command holds the terminal open. To run it detached, with no console
window at all, use the launcher instead — double-click `run.cmd`, or:

```powershell
.\run.cmd
```

`run.cmd` starts the widget with `pythonw.exe`, the console-less build of the
same interpreter that ships with Python, and exits immediately. Close the
terminal afterwards and the widget keeps running. Right-click the panel →
**Create desktop shortcut** for a normal double-click launcher, or **Start with
Windows** to have it come up at login. Both point at `pythonw.exe`, so no
terminal is ever involved and there is nothing packaged for antivirus to flag.

## Build a standalone .exe

```powershell
.\build.ps1            # produces dist\ClaudeUsageWidget.exe
.\build.ps1 -Install   # and adds it to the Startup folder
.\build.ps1 -OneDir    # unpacked folder build
.\build.ps1 -Trim      # smaller bundle, drops unused Qt modules
```

`-Trim` roughly halves the bundle by excluding Qt modules this code never
imports. It is off by default because excluding a module can also drop a DLL
that QtGui or QtWidgets links against, and the resulting executable fails at
launch with "the ordinal N could not be located in the dynamic link library".
If a trimmed build won't start, rebuild without `-Trim`.

**You do not have to build an executable.** Antivirus software frequently
quarantines PyInstaller onefile builds, because unpacking and executing at
runtime is also what malware packers do. Running from source avoids the issue
entirely and still supports autostart: the widget's "Start with Windows" option
registers `pythonw.exe -m claude_usage_widget`, which launches with no console
window and nothing packed. If you do want an executable, `-OneDir` is flagged
far less often than the default onefile build.

## Using it

| Action | How |
| --- | --- |
| Move it | Drag the panel anywhere; the position is remembered |
| Refresh now | Double-click the panel, or right-click → Refresh now |
| Hide / show | Click the tray icon |
| Autostart | Right-click → Start with Windows |
| Desktop shortcut | Right-click → Create desktop shortcut |
| Opacity | Right-click → Opacity |
| Open full usage page | Click the `→` in the header |
| Quit | Right-click → Quit |

Autostart is a shortcut in your Startup folder, created via `WScript.Shell` —
no registry writes, and toggling it off deletes the shortcut.

## Where the data comes from

Claude Code has no public API for plan usage
([anthropics/claude-code#32796](https://github.com/anthropics/claude-code/issues/32796)),
so the widget calls the same undocumented endpoint the `/usage` panel uses:

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken>
anthropic-beta: oauth-2025-04-20
User-Agent: claude-code/<version>
```

The token is read from `%USERPROFILE%\.claude\.credentials.json`, which is where
Claude Code stores it on Windows
([docs](https://code.claude.com/docs/en/authentication)). The file is read fresh
on every poll and the token never leaves your machine except in the request
above. If you set `CLAUDE_CONFIG_DIR`, that location is used instead.

### Signing in

**The widget has no login screen, and does not need one.** It never handles your
Google, email, or Anthropic credentials, and implements no OAuth flow of its own.

You sign in once in Claude Code — `claude`, then `/login`, which opens a browser
where "Continue with Google" works exactly as it always does. That flow hands
Claude Code an OAuth token, Claude Code writes it to `.credentials.json`, and
the widget reads that file. However you authenticated is invisible to the
widget; all it ever sees is the resulting token.

The one consequence: **Claude Code owns token refresh.** It renews the token
whenever you use it, so under normal use the widget just keeps working. If you
do not run Claude Code for long enough that the token expires, the widget says
"Sign-in expired — open Claude Code to refresh it" and keeps checking every 5
minutes; opening Claude Code clears it. The widget deliberately never writes to
the credentials file, so it cannot disturb your Claude Code session.

### Two caveats worth knowing

**The endpoint rate-limits aggressively.** Polling every 30–60s can get you
stuck in persistent 429s with no `Retry-After`
([#31637](https://github.com/anthropics/claude-code/issues/31637)). The 5-minute
default is well clear of that, and on any failure the widget doubles its
interval up to 30 minutes, keeps showing the last known values, and puts the
reason in a footer line rather than going blank.

**It is undocumented, so field names could change.** The parser reads the
response's `limits` array, which names each cap by `kind` and `scope` — that is
where per-model weekly limits live, since the matching top-level keys
(`seven_day_opus` and friends) are `null`. Credits come from `spend`, whose
amounts are in **minor units**: `{"amount_minor": 1388, "exponent": 2}` is
$13.88. The response also carries placeholder objects for unreleased features,
all-null at 0%, which are filtered out rather than rendered as empty bars.

If the `limits` array is ever absent, the parser falls back to top-level
`five_hour` / `seven_day` keys, matching several spellings each and normalising
0–1 fractions to percentages. The plan name is not in the response at all; it is
derived from `rateLimitTier` in the credentials file, so
`default_claude_max_5x` displays as "Max (5x)".

If the rows ever stop looking right:

```powershell
python -m claude_usage_widget.probe
```

That prints the credentials layout, token expiry, the exact request, and the raw
JSON alongside how the widget interpreted it. Secrets are redacted — tokens show
only their last six characters — so the output is safe to paste into an issue.
Remapping is then a small edit to `LIMIT_KIND_LABELS` or `WINDOW_SPECS` in
`claude_usage_widget/usage_api.py`. A captured response is checked in at
`tests/fixtures/usage_response.json` and the parser is tested against it.

## Why not a real Windows 11 "widget"?

The Windows Widgets Board requires an MSIX-packaged app with a widget provider,
and widgets there live inside the board panel — they cannot float on top of your
other windows. Since always-on-top was the point, this is a frameless
`WS_EX_TOPMOST` desktop panel instead, kept off the taskbar and Alt-Tab so it
behaves like a widget rather than an app window.

## Configuration

Settings live in `%APPDATA%\ClaudeUsageWidget\config.json` and are written when
you change something from the menu. Editable by hand (restart to apply):

| Key | Default | Meaning |
| --- | --- | --- |
| `poll_seconds` | `300` | Refresh interval; floored at 60 |
| `warn_percent` | `70` | Bar turns yellow at or above this |
| `critical_percent` | `85` | Bar turns red at or above this |
| `notify_at` | `[70, 80]` | Toast thresholds |
| `notifications_enabled` | `true` | Master switch for toasts |
| `opacity` | `0.96` | Window opacity |
| `position` | `null` | Saved `[x, y]`; ignored if off-screen |
| `user_agent` | `null` | Override; otherwise autodetected from the CLI |

A threshold only toasts once per window. When the window resets and usage drops
back below it, the threshold re-arms.

## Troubleshooting

**"No credentials at …"** — run `claude` in a terminal and sign in with
`/login`.

**"Sign-in expired …"** or **"Token rejected"** — run `claude` once and the CLI
refreshes the token in place. The widget checks the token's expiry before each
request, so it reports this without spending a doomed call against the rate
limit, and it keeps retrying at the normal 5-minute cadence rather than backing
off.

**"Rate limited"** — the widget is already backing off. It will recover on its
own; the bars keep showing the last good values meanwhile.

**Rows look wrong or are missing** — run the probe command above.

**"The ordinal N could not be located in the dynamic link library"** — read
which file the dialog names.

If it names `ClaudeUsageWidget.exe` itself, the packaged bundle is incomplete.
Rebuild without `-Trim`, or use `-OneDir`, or skip packaging and run from source.

If it names a `Qt6*.dll`, Windows loaded another application's Qt instead of
PySide6's. Run `where Qt6Core.dll`: a path outside your PySide6 folder is on your
system PATH and shadowing the right one. A virtual environment usually resolves
it; otherwise remove that PATH entry. The same message can also mean a missing
Visual C++ runtime — install the
[latest Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe) —
or a DLL your antivirus quarantined, in which case check its history and
reinstall PySide6.

## What it does with your credentials

This widget reads your Claude Code OAuth token, so it is worth being precise
about what happens to it.

- It reads `%USERPROFILE%\.claude\.credentials.json` and sends the access token
  to `https://api.anthropic.com/api/oauth/usage`. That is the only network
  request the program ever makes.
- It **never writes** to the credentials file. Token refresh belongs to Claude
  Code: refresh tokens rotate, so consuming one here could invalidate your CLI
  session. When the token expires the widget says so and waits.
- No telemetry, no analytics, no crash reporting, no update check.
- Settings go to `%APPDATA%\ClaudeUsageWidget\config.json`, which holds no
  secrets — only window position, thresholds, and which notifications have
  fired.
- `probe` redacts its output: tokens appear only as their last six characters
  and long strings become `<str len=N>`, so it is safe to paste into an issue.

The relevant code is short and worth reading yourself before you trust it:
[`credentials.py`](claude_usage_widget/credentials.py) and
[`usage_api.py`](claude_usage_widget/usage_api.py).

## Verifying a release

Released binaries are unsigned, so Windows SmartScreen and some antivirus
products will warn about them. Rather than a code-signing certificate, releases
carry a [build provenance attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds):

```powershell
gh attestation verify ClaudeUsageWidget-*.zip --repo <owner>/claude_windows_widget
(Get-FileHash ClaudeUsageWidget-*.zip -Algorithm SHA256).Hash.ToLower()
```

That proves the binary was built by this repository's workflow from a specific
commit — a stronger claim than a signature, which only says someone paid a
certificate authority. Every release is built on a clean GitHub runner from
tagged source, never uploaded from a developer machine.

If you would rather not trust a prebuilt binary at all, run from source. It
takes one `pip install` and behaves identically.

## Tests

```powershell
python -m unittest discover -s tests
```

The parser tests are Qt-free; the widget and notification tests run headless via
Qt's offscreen platform and skip themselves if PySide6 is not installed.
