# Calendar Analyzer

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

This project uses `uv`, a fast Python package installer and resolver. Install it using one of these methods:

### Using curl (macOS/Linux)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Using pip

```bash
pip install uv
```

## Setup

1. Make sure you have Python 3.7+ installed
2. Create and activate a virtual environment:

   ```bash
   # Create a new virtual environment
   uv venv
   
   # Activate the virtual environment
   source .venv/bin/activate  # On macOS/Linux
   ```

3. Install the required dependencies using uv:

   ```bash
   uv pip install -r requirements.txt
   ```

## Usage

Make sure your virtual environment is activated, then run the script:

Basic usage (analyzes past year):

```bash
python calendar_analyzer.py
```

Analyze a specific date range:

```bash
python calendar_analyzer.py --start-date 2024-01-01 --end-date 2024-03-31
```

Customize the number of days to look back:

```bash
python calendar_analyzer.py --days 90  # Analyze last 90 days
```

Specify a custom calendar file:

```bash
python calendar_analyzer.py --calendar /path/to/your/calendar.ics
```

The script will automatically:

1. Find your most recent calendar file (unless specified)
2. Analyze meetings from the specified date range
3. Display a summary of the findings

## Note

This script reads your local calendar data and does not send any information to external servers. All processing is done locally on your machine.
