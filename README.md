# 9router-antigravity-tray

A lightweight Windows system tray monitor for **OmniRoute** and **9Router** Antigravity accounts. It tracks aggregated Gemini and Claude pool quotas across multiple accounts, supports remote cloud servers and local routers, and provides a built-in GUI settings dialog.

[English](README.md) | [Русский](README_RU.md)

## Why this exists

This tool displays Antigravity quota stats directly in the Windows system tray near the clock. It connects to your remote OmniRoute server or local 9Router instance, tallies total pool percentages, and tracks quotas without needing to open the web UI.

## Features

- **Dual router support**: Connects to remote **OmniRoute** servers (via API key / Management Token) or local **9Router** / **OmniRoute** instances.
- **GUI Settings Dialog**: Double-click the tray icon to open a native Windows 11 dark-mode settings dialog with instant connection testing.
- **Quick Source Switching**: Switch between cloud server and local router directly from the tray context menu in 1 click.
- **Aggregated pool quotas**: Sums up 5-hour session and weekly quotas across all active Antigravity accounts.
- **Native Windows 11 styling**: Dark context menu, monochrome icons (Logo '9', Logo 'Ω', digits %, or combo).
- **Per-Monitor V2 DPI awareness**: Crisp rendering on 125%, 150%, and 200% scaling displays.
- **Non-blocking & lightweight**: Polls accounts concurrently in ~1 second with ~15 MB RAM usage.
- **Single-instance lock**: Named Win32 mutex prevents duplicate processes.

## Getting started

### Requirements
- Windows 10 or 11
- Python 3.9+
- 9Router installed and running locally

### Installation

1. Clone or download the repository:
   ```bash
   git clone https://github.com/arturg-sudo/9router-antigravity-tray.git
   cd 9router-antigravity-tray
   ```

2. Run `install.bat`:
   - Installs `pystray` and `pillow`.
   - Adds silent background startup to your Windows Startup folder.
   - Starts the monitor immediately.

Alternatively, install dependencies manually with `pip install -r requirements.txt` and launch via `wscript.exe run.vbs`.

## Usage

Right-click the tray icon near the clock:
- **Quotas and timers**: View aggregated pool percentage and minutes left until reset for Gemini and Claude.
- **Accounts**: Expand the submenu to see individual account statuses and percentages.
- **Icon style**: Switch between logo only, percentage only, or logo with percentage.
- **Refresh now**: Trigger an immediate update.
- **Exit**: Stop the monitor.

## Configuration

Settings are saved locally to `~/.hermes/antigravity_tray_config.json`. You can adjust polling interval, timer format, or icon style directly through the menu or in the config file.

## License

MIT
