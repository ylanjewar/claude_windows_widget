# Claude Usage Widget

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

## Build a standalone .exe

```powershell
.\build.ps1            # produces dist\ClaudeUsageWidget.exe
.\build.ps1 -Install   # and adds it to the Startup folder
```

The build trims the Qt modules the widget never loads, which roughly halves the
bundle. If a build ever misbehaves, remove the `$excludes` block in `build.ps1`
and rebuild — that trades size for certainty.

## Using it

| Action | How |
| --- | --- |
| Move it | Drag the panel anywhere; the position is remembered |
| Refresh now | Double-click the panel, or right-click → Refresh now |
| Hide / show | Click the tray icon |
| Autostart | Right-click → Start with Windows |
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

### Two caveats worth knowing

**The endpoint rate-limits aggressively.** Polling every 30–60s can get you
stuck in persistent 429s with no `Retry-After`
([#31637](https://github.com/anthropics/claude-code/issues/31637)). The 5-minute
default is well clear of that, and on any failure the widget doubles its
interval up to 30 minutes, keeps showing the last known values, and puts the
reason in a footer line rather than going blank.

**It is undocumented, so field names could change.** The parser is deliberately
forgiving: it matches several spellings per row, normalises 0–1 fractions to
percentages, and renders any unrecognised usage window with a generic label
instead of dropping it. If the rows ever stop looking right:

```powershell
python -m claude_usage_widget.probe
```

That prints the raw JSON and how the widget interpreted it, so remapping is a
one-line edit to `WINDOW_SPECS` in `claude_usage_widget/usage_api.py`.

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

**"Token rejected"** — the access token expired. Claude Code refreshes it when
you use it, so running `claude` once fixes this. The widget does not perform
token refresh itself, deliberately: it only ever reads the credentials file.

**"Rate limited"** — the widget is already backing off. It will recover on its
own; the bars keep showing the last good values meanwhile.

**Rows look wrong or are missing** — run the probe command above.

## Tests

```powershell
python -m unittest discover -s tests
```

The parser tests are Qt-free; the widget and notification tests run headless via
Qt's offscreen platform and skip themselves if PySide6 is not installed.
