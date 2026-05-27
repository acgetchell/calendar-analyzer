# Calendar Analyzer

[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](https://github.com/acgetchell/calendar-analyzer/blob/main/LICENSE)
[![CI](https://github.com/acgetchell/calendar-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/ci.yml)
[![codecov](https://codecov.io/github/acgetchell/calendar-analyzer/graph/badge.svg?token=UWRe2AcNnm)](https://codecov.io/github/acgetchell/calendar-analyzer)
[![CodeQL Advanced](https://github.com/acgetchell/calendar-analyzer/actions/workflows/codeql.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/codeql.yml)

A small local script that analyzes Apple Calendar and Outlook calendar exports and summarizes your meetings.

## Features

- Analyzes calendar events from a specified date range (defaults to past year)
- Reads Apple Calendar `.ics`, `.icbu`, and `.sqlitedb` exports
- Reads Microsoft Outlook for Mac `.olm` archives and Outlook `.ics` or `.csv` exports
- Does not read Outlook `.PST` files by design; use `.ics` for calendar-only exports
- Saves normalized meeting data as a Polars-readable Parquet cache for faster repeated reports
- Provides statistics on:
  - Total number of meetings
  - Total meeting hours
  - Average meetings per day
  - Average meeting duration
  - Most common meeting times
  - Most frequent meeting titles

## Quick Start

Install or verify `uv`, then install the analyzer command from this repository:

```bash
uv tool install .
```

Export a calendar file, then run:

```bash
calendar-analyzer --calendar ~/Documents/your-calendar.ics
```

## AI Analysis And Privacy

Calendar Analyzer runs locally on your machine. It reads your exported calendar file, writes a local Parquet cache for
faster follow-up reports, and does not send your calendar data to external servers.

If you want AI help summarizing a week, understanding how your meeting time was spent, or drafting year-end
accomplishment bullets from your calendar summary, generate a paste-ready prompt file and review it before sharing it
with ChatGPT, Claude, or another AI tool:

```bash
calendar-analyzer --start-date 2025-05-01 --end-date 2026-04-30 --generate-prompt
```

The prompt can include private meeting titles because the default summary intentionally reports your most frequent
meeting titles. It is currently based on meeting titles, dates, times, and durations; meeting notes, locations, and
attendees are not imported. Run the analyzer once with `--calendar` first so the local Parquet cache exists. By default,
the prompt is saved to `calendar-prompt.txt`.

## Exporting Your Calendar

### Apple Calendar

1. Open Calendar on your Mac.
2. Select the calendar you want to analyze.
3. Choose File > Export > Export.
4. Save the `.ics` file.

### Microsoft Outlook for Mac

1. Open Microsoft Outlook for Mac.
2. Choose File > Export.
3. Select Calendar in the export options. You can include other item types too; the analyzer reads the calendar items from the archive.
4. Continue through the export wizard and save the Microsoft Outlook for Mac archive (`.olm`) file.
5. If Outlook asks whether to delete exported items, choose the option to keep them in Outlook.

### Microsoft Outlook for Windows

Export the calendar as an `.ics` file:

1. Open Microsoft Outlook for Windows and switch to Calendar.
2. Select the calendar you want to analyze.
3. Use File > Save Calendar, or the calendar sharing/export option available in your Outlook version.
4. Choose an `.ics` calendar file and save it somewhere easy to find, such as Documents.

Do not export a `.PST` file for this tool. The analyzer intentionally does not read PST files because a PST export can
include mail, contacts, tasks, and other mailbox data, not just calendar information. Use an `.ics` calendar export
instead.

Outlook `.csv` calendar exports are also supported when available. CSV files are only imported when you pass
them explicitly with `--calendar` because generic CSV files are common in Documents and Downloads.

Run the analyzer with the exported file:

```bash
calendar-analyzer --calendar ~/Documents/your-calendar.ics
```

On Windows PowerShell:

```powershell
calendar-analyzer --calendar "$HOME\Documents\your-calendar.ics"
```

After the first import, the analyzer saves a Parquet cache. Later reports, including AI prompts, read from that cache
instead of re-importing the calendar export, so they run faster.

## Usage

```bash
# Basic usage (analyzes past year)
calendar-analyzer

# Analyze specific date range
calendar-analyzer --start-date 2024-01-01 --end-date 2024-03-31

# Analyze last 90 days
calendar-analyzer --days 90

# Specify custom calendar file
calendar-analyzer --calendar /path/to/your/calendar.olm

# Force re-importing the newest discovered calendar and refreshing the default cache
calendar-analyzer --import

# Force re-importing a specific calendar and refreshing its default cache
calendar-analyzer --calendar /path/to/your/calendar.olm --import

# Force re-importing a specific calendar into a specific saved cache
calendar-analyzer \
  --calendar "/path/to/your/calendar.ics" \
  --dataframe ~/.cache/calendar-analyzer/meetings.parquet \
  --import

# Use a specific saved Polars/Parquet data file
calendar-analyzer --dataframe /path/to/meetings.parquet

# Show top 10 meeting titles
calendar-analyzer --titles 10

# Show top 8 common meeting times
calendar-analyzer --times 8

# Exclude meeting titles by case-insensitive regex
calendar-analyzer --exclude-title 'SVM|VMTH'

# Repeat exclusions for separate title patterns
calendar-analyzer --exclude-title 'SVM' --exclude-title 'VMTH|State of the Hospital'

# Save results to file
calendar-analyzer --output analysis.txt

# Save a paste-ready AI analysis prompt for ChatGPT or Claude
calendar-analyzer --start-date 2024-01-01 --end-date 2024-01-07 --generate-prompt
```

The script looks for saved Polars/Parquet meeting data first, analyzes the requested date range from that data when
available, and imports a calendar export when the saved data is missing or stale. By default, the cache is
`~/.cache/calendar-analyzer/meetings.parquet`.

Use `--import` when you want to ignore the existing saved Polars/Parquet file and rebuild it from the calendar export.
Without `--calendar`, `--import` discovers the newest supported calendar export and refreshes the default cache. With
`--calendar`, it imports that specific file. Add `--dataframe` when you want the refreshed cache written to a specific
Parquet path.

On Windows, the default cache without `--calendar` is stored under
`%LOCALAPPDATA%\calendar-analyzer\meetings.parquet`. Reports show both the imported data coverage and the requested query
date range so you can tell whether an empty or small result came from the query window or from the calendar export
itself.

## Installation

### To Run Reports

Minimum setup:

- `uv`
- This repository checkout
- A supported calendar export file

Install the analyzer as a local command:

```bash
uv tool install .
```

Then run:

```bash
calendar-analyzer --calendar /path/to/your/calendar.ics
```

`uv tool install .` reads `pyproject.toml`, uses Python 3.11 or newer, and installs the runtime packages needed by the
analyzer. If `uv` says the tool directory is not on your `PATH`, run `uv tool update-shell`, restart your shell, and try
`calendar-analyzer --help`.

For a one-off run without installing the command, use:

```bash
uv run --no-dev calendar-analyzer --calendar /path/to/your/calendar.ics
```

`just` is not needed for running reports. It is part of the development workflow below.

### To Develop

Development uses `uv` for Python dependencies and `just` for local workflows. Install `just` with `uv`, then let the
repository recipe install or verify the rest of the development tools and sync development dependencies:

```bash
uv tool install rust-just
just setup
```

Run the local CI checks when setup finishes:

```bash
just ci
```

If `uv` says the tool directory is not on your `PATH`, run `uv tool update-shell`, restart your shell, and try
`just --version`.

The platform setup scripts are still available as all-in-one bootstraps. They install or verify `uv`, `just`, linters,
script checkers, security scanners, and development dependencies. On Windows, the setup script also installs Git for
Windows so the Bash-backed `just` recipes work from PowerShell.

```bash
# macOS
scripts/setup-macos.sh
```

```powershell
# Windows
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-windows.ps1
```

Both scripts run `just ci` by default. Use `--no-check` on macOS or `-NoCheck` on Windows to only install and sync
dependencies. See [scripts/README.md](scripts/README.md) for script linting and formatting details.

## Development

### Workflows

Common recipes are listed alphabetically:

```bash
# Run Ruff, Ty, typos, TOML checks, script checks, and tests
just check

# Run local CI checks without generating coverage
just ci

# Generate coverage.xml and terminal coverage
just coverage

# Apply Ruff, Taplo, and shell script auto-fixes
just fix

# Run the analyzer
just run --days 90

# Run pip-audit, repository Semgrep rules, and zizmor
just security

# Install or verify development tools and sync dependencies
just setup

# Run tests only
just test
```

The local workflow mirrors CI: `just ci` runs all checks and security scans. Coverage is generated separately with `just coverage` and uploaded by the Codecov workflow.

### Dependency Management

Uses `uv` with `pyproject.toml`. Dependencies organized as main (runtime) and development (tools).

```bash
# Add dependencies
uv add package-name
uv add --group dev package-name

# Update dependencies
uv sync --group dev  # all
uv sync              # main only
```

[Dependabot](https://dependabot.com/) monitors for updates automatically.

### Spell Checking

Uses [typos-cli](https://github.com/crate-ci/typos):

```bash
just spell-check
```

Configured in `typos.toml`.
