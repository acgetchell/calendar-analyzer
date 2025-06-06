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

## Exporting Your Calendar

To analyze your calendar, you'll need to export it first:

1. Open the Calendar app on your Mac
2. Select the calendar(s) you want to analyze
3. Go to File > Export
4. Choose your Documents folder as the save location
5. Save the file (it will be saved as a `.ics` file)
6. Run the analyzer with the path to your exported file:

   ```bash
   python calendar_analyzer.py --calendar ~/Documents/your-calendar.ics
   ```

The script will automatically look in your Documents folder, but you can specify any location where you've saved your calendar export.

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

## Development

### Spell Checking

This project uses [cspell](https://cspell.org/) for spell checking. To use it:

1. Install cspell:

   ```bash
   npm install -g cspell
   ```

2. Run spell check:

   ```bash
   cspell "**/*.{md,py,txt}"
   ```

The spell check configuration is in `cspell.json`. Add any project-specific words to the `words` array in this file.

#### Cursor Integration

The project includes cspell integration for Cursor (and VS Code). To enable it:

1. Install the "Code Spell Checker" extension in Cursor
2. The project's `.vscode/settings.json` file is already configured to:
   - Enable spell checking for Markdown, Python, and text files
   - Use the project's `cspell.json` as a custom dictionary
   - Allow adding new words to the dictionary
   - Ignore common build and cache directories

You can add new words to the dictionary by:

1. Right-clicking on a misspelled word
2. Selecting "Add to Dictionary"
3. Choosing "project-words" as the dictionary

## Note

This script reads your local calendar data and does not send any information to external servers. All processing is done locally on your machine.
