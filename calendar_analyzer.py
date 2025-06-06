#!/usr/bin/env python3
"""
Calendar Analyzer - A tool to analyze calendar events and generate summaries.

This module provides functionality to parse calendar files (ICS and SQLite formats)
and generate detailed statistics about meetings and events.
"""
import sys
import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from icalendar import Calendar
import pandas as pd
from dateutil import tz

# Define timezone constants
PACIFIC = tz.gettz('America/Los_Angeles')
UTC = tz.UTC

def convert_to_pacific(dt):
    """Convert a datetime to Pacific time."""
    if dt.tzinfo is None:
        # If no timezone info, assume UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(PACIFIC)

def print_calendar_export_instructions():
    """Print instructions for exporting a calendar file."""
    print("\nPlease export your calendar from the Calendar app:")
    print("1. Open the Calendar app")
    print("2. Select the calendar(s) you want to analyze")
    print("3. Go to File > Export")
    print("4. Save the calendar file")
    print("\nThen run this script with the path to your exported file:")
    print("python calendar_analyzer.py --calendar /path/to/your/calendar.ics")

def get_calendar_path(calendar_file=None):
    """Get the path to the calendar file."""
    if calendar_file:
        try:
            calendar_path = Path(calendar_file).resolve()
            print(f"Looking for calendar at: {calendar_path}")
            print(f"Path exists: {calendar_path.exists()}")
            if calendar_path.exists():
                print(f"Is directory: {calendar_path.is_dir()}")
                if calendar_path.is_dir():
                    print("Directory contents:")
                    for item in calendar_path.iterdir():
                        print(f"  - {item.name}")
            return calendar_path
        except OSError as e:
            print(f"Error processing path: {e}")
            sys.exit(1)

    # If no file specified, try to find calendar files in common locations
    home = Path.home()
    possible_paths = [
        home / "Library/Calendars",  # Default macOS Calendar location
        home / "Library/Application Support/Calendar",  # Alternative location
        home / "Library/Application Support/Apple/Calendar",  # Another possible location
        home / "Documents",  # Common export location
        home / "Downloads"   # Common export location
    ]

    print("\nSearching for calendar files in:")
    for path in possible_paths:
        print(f"- {path}")
        if path.exists():
            print("  ✓ Directory exists")
            # List all calendar files in this directory and subdirectories
            calendar_files = []
            for ext in ['*.ics', '*.icbu', '*.sqlitedb']:  # Search for calendar files
                calendar_files.extend(list(path.rglob(ext)))

            if calendar_files:
                print(f"  ✓ Found {len(calendar_files)} calendar files")
                for file in calendar_files[:5]:  # Show first 5 files
                    print(f"    - {file}")
                if len(calendar_files) > 5:
                    print(f"    ... and {len(calendar_files) - 5} more")
            else:
                print("  ✗ No calendar files found")
        else:
            print("  ✗ Directory does not exist")

    # Try to find calendar files in all possible locations
    all_calendar_files = []
    for path in possible_paths:
        if path.exists():
            for ext in ['*.ics', '*.icbu', '*.sqlitedb']:
                all_calendar_files.extend(list(path.rglob(ext)))

    if not all_calendar_files:
        print("\nError: No calendar files found in any of the expected locations.")
        print_calendar_export_instructions()
        sys.exit(1)

    # Get the most recent calendar file
    latest_calendar = max(all_calendar_files, key=lambda x: x.stat().st_mtime)
    print(f"\nSelected most recent calendar file: {latest_calendar}")
    return latest_calendar

def analyze_calendar(calendar_path, start_date=None, end_date=None, days_back=365):
    """Analyze calendar events from the specified date range."""
    try:
        # Handle different calendar file formats
        if calendar_path.suffix.lower() == '.sqlitedb':
            return analyze_sqlite_calendar(calendar_path, start_date, end_date)
        if calendar_path.suffix.lower() == '.icbu':
            # .icbu files are actually directories containing calendar data
            # Look for SQLite database first, then fall back to ICS files
            sqlite_db_path = calendar_path / 'Calendar.sqlitedb'
            if sqlite_db_path.exists():
                print(f"Found SQLite database in ICBU backup: {sqlite_db_path}")
                return analyze_sqlite_calendar(sqlite_db_path, start_date, end_date)

            # Look for ICS files as fallback
            if ics_files := list(calendar_path.glob('*.ics')):
                calendar_path = ics_files[0]  # Use the first ICS file found
                print(f"Found ICS file in ICBU backup: {calendar_path}")
            else:
                print(f"Error: Could not find calendar data (SQLite or ICS) in {calendar_path}")
                # List what's actually in the directory to help debug
                print("Contents of ICBU directory:")
                try:
                    for item in calendar_path.iterdir():
                        print(f"  - {item.name}")
                except OSError as e:
                    print(f"  Error listing directory contents: {e}")
                sys.exit(1)

        with open(calendar_path, 'rb') as f:
            cal = Calendar.from_ical(f.read())

        # Calculate date range
        if end_date is None:
            end_date = datetime.now(PACIFIC)
        if start_date is None:
            start_date = end_date - timedelta(days=days_back)

        # Initialize data structures
        meetings = []
        meeting_stats = defaultdict(int)

        # Process events
        for event in cal.walk('VEVENT'):
            start = event.get('dtstart').dt
            if isinstance(start, datetime):
                # Convert to Pacific time
                start = convert_to_pacific(start)
                if start_date <= start <= end_date:
                    summary = str(event.get('summary', 'No Title'))
                    duration = event.get('duration', timedelta(hours=1))
                    if isinstance(duration, timedelta):
                        duration_hours = duration.total_seconds() / 3600
                    else:
                        duration_hours = 1

                    meetings.append({
                        'date': start.date(),
                        'time': start.time(),
                        'summary': summary,
                        'duration_hours': duration_hours
                    })

                    # Update stats
                    meeting_stats['total_meetings'] += 1
                    meeting_stats['total_hours'] += duration_hours

        return meetings, meeting_stats

    except OSError as e:
        print(f"Error reading calendar file: {e}")
        sys.exit(1)

def analyze_sqlite_calendar(calendar_path, start_date=None, end_date=None):
    """Analyze calendar events from a SQLite database."""
    try:
        conn = sqlite3.connect(calendar_path)
        cursor = conn.cursor()

        # Calculate date range
        if end_date is None:
            end_date = datetime.now(PACIFIC)
        if start_date is None:
            start_date = end_date - timedelta(days=365)

        # Convert dates to Apple's calendar format (seconds since 2001-01-01)
        apple_epoch = datetime(2001, 1, 1, tzinfo=UTC)
        start_seconds = int((start_date - apple_epoch).total_seconds())
        end_seconds = int((end_date - apple_epoch).total_seconds())

        # Query calendar events (no item_type filter)
        cursor.execute("""
            SELECT 
                summary,
                start_date,
                end_date
            FROM CalendarItem
            WHERE start_date >= ? AND start_date <= ?
        """, (start_seconds, end_seconds))

        meetings = []
        meeting_stats = defaultdict(int)

        for row in cursor.fetchall():
            summary, start_seconds, end_seconds = row
            # If you want to filter out non-events, add logic here
            # For now, we assume all rows are events

            # Convert Apple's calendar format to datetime
            start_dt = apple_epoch + timedelta(seconds=start_seconds)
            end_dt = apple_epoch + timedelta(seconds=end_seconds)

            # Convert to Pacific time
            start_dt = convert_to_pacific(start_dt)
            end_dt = convert_to_pacific(end_dt)

            # Calculate duration in hours
            duration_hours = (end_dt - start_dt).total_seconds() / 3600

            meetings.append({
                'date': start_dt.date(),
                'time': start_dt.time(),
                'summary': summary or 'No Title',
                'duration_hours': duration_hours
            })

            # Update stats
            meeting_stats['total_meetings'] += 1
            meeting_stats['total_hours'] += duration_hours

        conn.close()
        return meetings, meeting_stats

    except sqlite3.Error as e:
        print(f"Error reading SQLite calendar: {e}")
        sys.exit(1)

def generate_summary(meetings, stats, num_titles=50):
    """Generate a summary of the calendar analysis."""
    if not meetings:
        return "No meetings found in the specified time period."

    # Convert to DataFrame for easier analysis
    df = pd.DataFrame(meetings)

    # Calculate additional statistics
    avg_meetings_per_day = stats['total_meetings'] / 365
    avg_meeting_duration = stats['total_hours'] / stats['total_meetings']

    # Get current Pacific timezone info
    now = datetime.now(PACIFIC)
    is_dst = now.dst() != timedelta(0)
    timezone_name = "PDT" if is_dst else "PST"

    # Get date range
    start_date = df['date'].min()
    end_date = df['date'].max()
    date_range_days = (end_date - start_date).days

    # Generate summary
    summary = [
        f"📅 Calendar Analysis Summary (All times in Pacific Time - Currently {timezone_name})",
        "=" * 70,
        "\nDate Range:",
        f"- From: {start_date.strftime('%B %d, %Y')}",
        f"- To:   {end_date.strftime('%B %d, %Y')}",
        f"- Span: {date_range_days} days",
        "\nTimezone Information:",
        f"- Currently using {timezone_name} (Pacific {'Daylight' if is_dst else 'Standard'} Time)",
        "- All times are automatically adjusted for DST transitions",
        "- Meetings during DST periods are shown in PDT",
        "- Meetings during standard time are shown in PST",
        "\nMeeting Statistics:",
        f"- Total Meetings: {stats['total_meetings']}",
        f"- Total Meeting Hours: {stats['total_hours']:.1f}",
        f"- Average Meetings per Day: {avg_meetings_per_day:.1f}",
        f"- Average Meeting Duration: {avg_meeting_duration:.1f} hours",
        f"\nTop 5 Most Common Meeting Times ({timezone_name}):",
    ]

    # Add most common meeting times
    time_counts = df['time'].value_counts().head()
    summary.extend(f"- {time.strftime('%I:%M %p')}: {count} meetings" for time, count in time_counts.items())

    # Add most frequent meeting titles
    summary.extend([
        f"\nTop {num_titles} Most Frequent Meeting Titles:",
        "-" * 30
    ])

    # Get meeting title frequencies and sort
    title_counts = df['summary'].value_counts().head(num_titles)
    for title, count in title_counts.items():
        # Truncate long titles to keep the output readable
        display_title = title[:100] + "..." if len(title) > 100 else title
        summary.append(f"{count:4d} | {display_title}")

    return "\n".join(summary)

def main():
    """Main function to run the calendar analyzer."""
    parser = argparse.ArgumentParser(
        description='Analyze calendar events from a specified date range.'
    )
    parser.add_argument('--calendar', help='Path to the exported calendar file (.ics)')
    parser.add_argument('--start-date', help='Start date for analysis (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='End date for analysis (YYYY-MM-DD)')
    parser.add_argument(
        '--days', type=int, default=365, 
        help='Number of days to look back from end date (default: 365)'
    )
    parser.add_argument(
        '--titles', type=int, default=50,
        help='Number of meeting titles to display (default: 50)'
    )
    args = parser.parse_args()

    # Parse dates if provided
    start_date = None
    end_date = None
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, '%Y-%m-%d').replace(tzinfo=PACIFIC)
        except ValueError:
            print("Error: Start date must be in YYYY-MM-DD format")
            sys.exit(1)
    if args.end_date:
        try:
            end_date = datetime.strptime(args.end_date, '%Y-%m-%d').replace(tzinfo=PACIFIC)
        except ValueError:
            print("Error: End date must be in YYYY-MM-DD format")
            sys.exit(1)

    # Validate date range if both dates are provided
    if start_date and end_date and end_date < start_date:
        print("Error: End date cannot be before start date")
        print(f"Start date: {start_date.strftime('%Y-%m-%d')}")
        print(f"End date: {end_date.strftime('%Y-%m-%d')}")
        sys.exit(1)

    print("📊 Analyzing your calendar...")

    # Get calendar path
    calendar_path = get_calendar_path(args.calendar)
    print(f"Found calendar at: {calendar_path}")

    # Analyze calendar
    meetings, stats = analyze_calendar(calendar_path, start_date, end_date, args.days)

    # Generate and print summary
    summary = generate_summary(meetings, stats, args.titles)
    print("\n" + summary)


if __name__ == "__main__":
    main()
