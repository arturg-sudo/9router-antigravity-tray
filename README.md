# 9router-antigravity-tray

A lightweight Windows system tray monitor for 9Router Antigravity accounts. It tracks aggregated Gemini and Claude pool quotas across multiple accounts and shows the earliest reset timer.

[English](README.md) | [Русский](README_RU.md)

## Why this exists

If you run multiple Antigravity accounts in 9Router (5 or more), tracking when your pool resets or how much buffer you have left usually means opening the web UI or making manual requests. 

This tool puts that info in the system tray near the clock. It reads the local 9Router database and API directly on your machine, tallies the total pool percentage, and calculates the earliest reset time for active models.

## Features

- Sums up total pool quotas across all active Antigravity accounts (e.g., 650% Gemini across 10 accounts).
- Calculates the earliest reset time in minutes for models running low.
- Native Windows 11 dark context menu and monochrome tray icon.
- Runs with per-monitor DPI awareness so text stays sharp on 1440p and 4K displays.
- Non-blocking updates: polls accounts concurrently with a thread pool in 1-2 seconds.
- Single-instance lock via named Win32 mutex to prevent duplicate tray processes.
- Memory footprint stays around 15 MB RAM.

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
