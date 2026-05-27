# Calendar Analyzer

[![CI](https://github.com/acgetchell/calendar-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/ci.yml)
[![codecov](https://codecov.io/github/acgetchell/calendar-analyzer/graph/badge.svg?token=UWRe2AcNnm)](https://codecov.io/github/acgetchell/calendar-analyzer)
[![CodeQL Advanced](https://github.com/acgetchell/calendar-analyzer/actions/workflows/codeql.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/codeql.yml)

A simple Python script that analyzes Apple Calendar and Outlook calendar exports and summarizes your meetings.

## Features

- Analyzes calendar events from a specified date range (defaults to past year)
- Reads Apple Calendar `.ics`, `.icbu`, and `.sqlitedb` exports
- Reads Outlook for Mac `.olm` archives and Outlook `.ics` or `.csv` exports
- Does not read Outlook `.PST` files by design; use `.ics` for calendar-only exports
- Saves normalized meeting data as a Polars-readable Parquet cache for faster repeated reports
- Provides statistics on:
  - Total number of meetings
  - Total meeting hours
  - Average meetings per day
  - Average meeting duration
  - Most common meeting times
  - Most frequent meeting titles

## Prerequisites

This project uses `uv` for Python dependencies and `just` for local workflows. The setup scripts install or verify
the required tools, including script linters, and sync development dependencies.

```bash
# macOS
scripts/setup-macos.sh
```

```powershell
# Windows
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-windows.ps1
```

Both scripts run `just ci` by default. Use `--no-check` on macOS or `-NoCheck` on Windows to only install/sync dependencies.
See [scripts/README.md](scripts/README.md) for script linting and formatting details.

## Setup

1. **Run the platform setup script above**
2. **For later dependency refreshes:**

   ```bash
   just setup
   ```

## Exporting Your Calendar

### Apple Calendar

1. Open Calendar on your Mac.
2. Select the calendar you want to analyze.
3. Choose File > Export > Export.
4. Save the `.ics` file.

### Outlook for Mac

1. Open Outlook for Mac.
2. Choose File > Export.
3. Select Calendar in the export options. You can include other item types too; the analyzer reads the calendar items from the archive.
4. Continue through the export wizard and save the Outlook for Mac archive (`.olm`) file.
5. If Outlook asks whether to delete exported items, choose the option to keep them in Outlook.

Outlook `.ics` and `.csv` calendar exports are also supported when available. CSV files are only imported when you pass
them explicitly with `--calendar` because generic CSV files are common in Documents and Downloads.

### Outlook for Windows

Export the calendar as an `.ics` file:

1. Open Outlook and switch to Calendar.
2. Select the calendar you want to analyze.
3. Use File > Save Calendar, or the calendar sharing/export option available in your Outlook version.
4. Choose an `.ics` calendar file and save it somewhere easy to find, such as Documents.

Do not export a `.PST` file for this tool. The analyzer intentionally does not read PST files because a PST export can
include mail, contacts, tasks, and other mailbox data, not just calendar information. Use an `.ics` calendar export
instead.

Run the analyzer with the exported file:

```bash
just run --calendar ~/Documents/your-calendar.ics
```

On Windows PowerShell:

```powershell
just run --calendar "$HOME\Documents\your-calendar.ics"
```

## Usage

```bash
# Basic usage (analyzes past year)
just run

# Analyze specific date range
just run --start-date 2024-01-01 --end-date 2024-03-31

# Analyze last 90 days
just run --days 90

# Specify custom calendar file
just run --calendar /path/to/your/calendar.olm

# Force re-importing the newest discovered calendar and refreshing the default cache
just run --import

# Force re-importing a specific calendar and refreshing its default cache
just run --calendar /path/to/your/calendar.olm --import

# Force re-importing a specific calendar into a specific saved cache
just run \
  --calendar "/path/to/your/calendar.ics" \
  --dataframe ~/.cache/calendar-analyzer/meetings.parquet \
  --import

# Use a specific saved Polars/Parquet data file
just run --dataframe /path/to/meetings.parquet

# Show top 10 meeting titles
just run --titles 10

# Show top 8 common meeting times
just run --times 8

# Exclude meeting titles by case-insensitive regex
just run --exclude-title 'SVM|VMTH'

# Repeat exclusions for separate title patterns
just run --exclude-title 'SVM' --exclude-title 'VMTH|State of the Hospital'

# Save results to file
just run --output analysis.txt
```

The script looks for saved Polars/Parquet meeting data first, analyzes the requested date range from that data when
available, and imports a calendar export when the saved data is missing or stale. With `--calendar`, the default cache is
created next to the calendar export, such as `calendar.olm.parquet`. Without `--calendar`, the default cache is
`~/.cache/calendar-analyzer/meetings.parquet`.

Use `--import` when you want to ignore the existing saved Polars/Parquet file and rebuild it from the calendar export.
Without `--calendar`, `--import` discovers the newest supported calendar export and refreshes the default cache. With
`--calendar`, it imports that specific file. Add `--dataframe` when you want the refreshed cache written to a specific
Parquet path, such as the default `~/.cache/calendar-analyzer/meetings.parquet`.

On Windows, `scripts/setup-windows.ps1` installs Git for Windows so the Bash-backed `just` recipes work from PowerShell
too. The default cache without `--calendar` is stored under `%LOCALAPPDATA%\calendar-analyzer\meetings.parquet`. Reports
show both the imported data coverage and the requested query date range so you can tell whether an empty or small result
came from the query window or from the calendar export itself.

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

## Note

This script reads your local calendar data and does not send any information to external servers. All processing is done locally on your machine.
