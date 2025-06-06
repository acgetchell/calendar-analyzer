# Calendar Analyzer

A simple Python script that analyzes your Apple Calendar data from the past year and provides a summary of your meetings.

## Features

- Analyzes calendar events from the past year
- Provides statistics on:
  - Total number of meetings
  - Total meeting hours
  - Average meetings per day
  - Average meeting duration
  - Most common meeting times

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
```bash
python calendar_analyzer.py
```

The script will automatically:
1. Find your most recent calendar file
2. Analyze meetings from the past year
3. Display a summary of the findings

## Note

This script reads your local calendar data and does not send any information to external servers. All processing is done locally on your machine. 