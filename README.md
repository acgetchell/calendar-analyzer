# Calendar Analyzer

[![CI](https://github.com/acgetchell/calendar-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/ci.yml)
[![codecov](https://codecov.io/github/acgetchell/calendar-analyzer/graph/badge.svg?token=UWRe2AcNnm)](https://codecov.io/github/acgetchell/calendar-analyzer)
[![CodeQL Advanced](https://github.com/acgetchell/calendar-analyzer/actions/workflows/codeql.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/codeql.yml)

A simple Python script that analyzes your Apple Calendar data and provides a summary of your meetings.

## Features

- Analyzes calendar events from a specified date range (defaults to past year)
- Provides statistics on:
  - Total number of meetings
  - Total meeting hours
  - Average meetings per day
  - Average meeting duration
  - Most common meeting times
  - Most frequent meeting titles

## Prerequisites

This project uses `uv` for Python dependencies and `just` for local workflows. The setup scripts install or verify the required tools, including script linters, and sync development dependencies.

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

1. Open the Calendar app on your Mac
2. Select the calendar(s) you want to analyze
3. Go to File > Export and save as `.ics` file to your Documents folder
4. Run the analyzer:

   ```bash
   uv run calendar-analyzer --calendar ~/Documents/your-calendar.ics
   ```

## Usage

```bash
# Basic usage (analyzes past year)
uv run calendar-analyzer

# Analyze specific date range
uv run calendar-analyzer --start-date 2024-01-01 --end-date 2024-03-31

# Analyze last 90 days
uv run calendar-analyzer --days 90

# Specify custom calendar file
uv run calendar-analyzer --calendar /path/to/your/calendar.ics

# Show top 10 meeting titles
uv run calendar-analyzer --titles 10

# Save results to file
uv run calendar-analyzer --output analysis.txt
```

The script automatically finds your most recent calendar file (unless specified), analyzes the date range, and displays or saves results.

## Development

### Workflows

Common recipes are listed alphabetically:

```bash
# Run Ruff, Ty, typos, TOML checks, script checks, and tests
just check

# Run the full local CI workflow
just ci

# Generate coverage.xml and terminal coverage
just coverage

# Apply Ruff, Taplo, and shell script auto-fixes
just fix

# Run pip-audit and repository Semgrep rules
just security

# Sync development dependencies
just setup

# Run tests only
just test
```

The local workflow mirrors CI: `just ci` runs all checks, security scans, and coverage.

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
