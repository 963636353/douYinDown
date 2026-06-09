# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Douyin (TikTok China) video downloader with a modular QFluentWidgets GUI. Supports browser QR-code login, a_bogus API signing, and concurrent video downloads. Designed for extensibility — add new platforms by implementing `BasePlatform`.

## Commands

```bash
# Activate venv
source venv/Scripts/activate

# Launch the GUI app
python main.py

# CLI fallback (original, still works)
python douyin_downloader.py

# Install/update core dependencies
pip install f2 requests undetected-chromedriver selenium PyQt-Fluent-Widgets
# Playwright fallback (optional):
pip install playwright
playwright install chromium
```

## Architecture

```
main.py                         — entry point: registers platforms, launches GUI
│
├── core/                       — platform-agnostic infrastructure
│   ├── base_platform.py        — BasePlatform ABC + VideoInfo dataclass
│   ├── cookie_manager.py       — Netscape-format cookie persistence (per platform)
│   ├── browser_login.py        — uc.Chrome / Playwright login orchestrator
│   ├── chromedriver_util.py    — auto-download & cache ChromeDriver
│   └── downloader.py           — streaming downloader with progress callback
│
├── platforms/                  — one package per platform
│   └── douyin/
│       ├── platform.py         — DouyinPlatform(BasePlatform)
│       ├── api.py              — a_bogus signing, param building, endpoints
│       └── url_parser.py       — extract_aweme_id, sanitize_filename
│
├── gui/                        — QFluentWidgets interface
│   ├── app.py                  — MainWindow (FluentWindow, left navigation, status bar)
│   ├── download_tab.py         — URL parse, download list, progress, concurrent dl
│   ├── login_tab.py            — QR login, cookie management
│   ├── settings_tab.py         — config.json editor (dir, proxy, concurrency)
│   └── widgets.py              — ProgressPanel, DownloadList
│
├── cookies/                    — per-platform cookie files (Netscape format)
├── downloads/                  — downloaded MP4 output
├── logs/                       — historical/runtime logs, ignored by git
└── config.json                 — user preferences (download dir, proxy, etc.)
```

## Adding a New Platform

1. Create `platforms/<name>/` with `platform.py`, `api.py`, `url_parser.py`
2. Implement `BasePlatform` in `platform.py`
3. Register in `main.py` by appending to the `platforms` list
4. Cookie manager and browser login work automatically via the base class contract

## Key Dependencies

- `f2` (v0.0.1.7) — provides `f2.utils.abogus` for a_bogus signing and browser fingerprint generation
- `undetected_chromedriver` — primary browser automation for QR login
- `selenium` — browser control layer
- `playwright` — fallback browser automation
- `PyQt-Fluent-Widgets` — QFluentWidgets GUI component library
- `requests` — HTTP client for API calls and streaming downloads

## Data Flow

1. **Login** → `LoginTab` → `browser_login.perform_browser_login(platform)` → browser opens → user scans QR → cookies saved to `cookies/<name>_cookies.txt`
2. **Parse URL** → `DownloadTab._parse_url` → `platform.extract_video_id(url)` → `platform.get_video_info(id, cookies)` → `VideoInfo` dataclass
3. **Download** → `download_video(url, path, headers, progress_cb)` → streams to `downloads/` with progress reported via callback → Qt signals update GUI

## Key Files

- `config.json` — user settings (download directory, proxy, concurrency)
- `cookies/<platform>_cookies.txt` — persisted login cookies
- Existing `douyin_cookies.txt` at root is auto-migrated to `cookies/douyin_cookies.txt` on first run
