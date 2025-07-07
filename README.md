# Calendar Analyzer

[![CI](https://github.com/acgetchell/calendar-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/ci.yml)
[![codecov](https://codecov.io/github/acgetchell/calendar-analyzer/graph/badge.svg?token=UWRe2AcNnm)](https://codecov.io/github/acgetchell/calendar-analyzer)
[![CodeQL Advanced](https://github.com/acgetchell/calendar-analyzer/actions/workflows/codeql.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/codeql.yml)
[![Pylint](https://github.com/acgetchell/calendar-analyzer/actions/workflows/pylint.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/pylint.yml)
[![Bandit](https://github.com/acgetchell/calendar-analyzer/actions/workflows/bandit.yml/badge.svg)](https://github.com/acgetchell/calendar-analyzer/actions/workflows/bandit.yml)

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

This project uses `uv`, a fast Python package installer and resolver. Install it using:

```bash
# Homebrew (recommended for macOS)
brew install uv

# Official installer
curl -LsSf https://astral.sh/uv/install.sh | sh

# Using pip
pip install uv
```

## Setup

1. **Ensure Python 3.9+ is installed**
2. **Create and activate a virtual environment:**

   ```bash
   uv venv
   source .venv/bin/activate  # On macOS/Linux
   ```

3. **Install dependencies:**

   ```bash
   # Main dependencies only
   uv sync
   
   # With development dependencies (for contributing)
   uv sync --group dev
   ```

## Exporting Your Calendar

1. Open the Calendar app on your Mac
2. Select the calendar(s) you want to analyze
3. Go to File > Export and save as `.ics` file to your Documents folder
4. Run the analyzer:

   ```bash
   python calendar_analyzer.py --calendar ~/Documents/your-calendar.ics
   ```

## Usage

Activate your virtual environment and run:

```bash
# Basic usage (analyzes past year)
python calendar_analyzer.py

# Analyze specific date range
python calendar_analyzer.py --start-date 2024-01-01 --end-date 2024-03-31

# Analyze last 90 days
python calendar_analyzer.py --days 90

# Specify custom calendar file
python calendar_analyzer.py --calendar /path/to/your/calendar.ics

# Show top 10 meeting titles
python calendar_analyzer.py --titles 10

# Save results to file
python calendar_analyzer.py --output analysis.txt
```

The script automatically finds your most recent calendar file (unless specified), analyzes the date range, and displays or saves results.

## Development

### Code Quality

Uses [pylint](https://pylint.org/) with minimum score 8.9/10:

```bash
pylint calendar_analyzer.py
```

### Testing

Uses [pytest](https://pytest.org/):

```bash
# Run all tests
pytest

# Verbose output
pytest -v

# Specific test file or function
pytest tests/test_calendar_analyzer.py
pytest tests/test_calendar_analyzer.py::test_analyze_mock_ics
```

### Security Scanning

Uses [Bandit](https://bandit.readthedocs.io/):

```bash
# Scan main application
bandit -r calendar_analyzer.py

# Scan tests (skip assert warnings)
bandit -r tests/ --skip B101
```

**Security Features:** No hardcoded secrets, secure file handling, input validation, local processing only.

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

Uses [cspell](https://cspell.org/):

```bash
# Install and run
npm install -g cspell
cspell "**/*.{md,py,txt}"
```

Configured in `cspell.json`. Includes Cursor/VS Code integration via "Code Spell Checker" extension.

## Note

This script reads your local calendar data and does not send any information to external servers. All processing is done locally on your machine.
