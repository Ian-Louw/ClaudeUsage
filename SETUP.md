# Claude Usage Widget — Setup (Windows 10)

A tray icon next to your clock showing your Claude Code usage. The icon displays your **weekly limit %** as a colored ring (green / orange / red). Hover it for session %, weekly %, and reset countdowns. Right-click for options.

When weekly usage crosses **85%**, the widget fires a Windows notification (once per crossing — it re-arms after usage drops back below 80%).

## 1. Install Python (if you don't have it)
Download from https://www.python.org/downloads/ — during install, tick **"Add Python to PATH"**.

## 2. Install the two libraries
Open Command Prompt and run:
```
pip install pystray pillow
```

## 3. Put the file somewhere permanent
e.g. `C:\Tools\claude_usage_widget.py`

## 4. Run it
```
pythonw C:\Tools\claude_usage_widget.py
```
`pythonw` (with the w) runs it without a console window. The icon appears in the tray — click the `^` arrow near the clock if Windows hid it, then drag it onto the taskbar to pin it.

## 5. Optional: start automatically with Windows
No registry edits needed — just a shortcut:
1. Press `Win+R`, type `shell:startup`, press Enter
2. Right-click in that folder → New → Shortcut
3. Target: `pythonw C:\Tools\claude_usage_widget.py`

Delete the shortcut anytime to undo.

## Requirements
- Claude Code installed and logged in (`claude` → `/login`) on a paid plan
- The widget reads the same login Claude Code already has — no API key, no extra account

## Troubleshooting
| Symptom | Fix |
|---|---|
| Grey `?` icon | Claude Code login not found — run `claude` and `/login` once |
| "Token expired" in tooltip | Open Claude Code once; it refreshes the token, widget recovers on next poll |
| "Rate limited" | Normal occasionally; the widget backs off and retries automatically |
| Claude Code runs in WSL | Set env var `CLAUDE_CREDENTIALS_PATH` to e.g. `\\wsl$\Ubuntu\home\<you>\.claude\.credentials.json` |

## Security notes (why this approach is safe)
- **One file, ~350 lines, plain Python** — you can read every line before running it
- Your token is read **read-only** from `%USERPROFILE%\.claude\.credentials.json`, held in memory, never logged or written anywhere
- **Only network destination:** `api.anthropic.com` over verified HTTPS — the same endpoint Claude Code's own `/usage` command uses
- No admin rights, no registry changes, no auto-update, no telemetry
- Polls every 5 minutes (10 after errors) to respect Anthropic's rate limits
- To uninstall: delete the .py file and the startup shortcut. Done.
