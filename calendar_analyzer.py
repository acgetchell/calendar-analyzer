"""Analyze Apple Calendar exports and summarize meeting patterns."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TypedDict
from zoneinfo import ZoneInfo

import pandas as pd
from icalendar import Calendar

if TYPE_CHECKING:
    from collections.abc import Iterable

PACIFIC = ZoneInfo("America/Los_Angeles")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
CALENDAR_PATTERNS = ("*.ics", "*.icbu", "*.sqlitedb")
DEFAULT_DAYS_BACK = 365
DEFAULT_DURATION_HOURS = 1.0


class Meeting(TypedDict):
    """Normalized calendar meeting data used for reporting."""

    date: date
    time: time
    summary: str
    duration_hours: float


class MeetingStats(TypedDict):
    """Aggregate meeting counters."""

    total_meetings: int
    total_hours: float


CalendarAnalysis = tuple[list[Meeting], MeetingStats]


def convert_to_pacific(dt: datetime) -> datetime:
    """Convert a datetime to Pacific time, treating naive values as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(PACIFIC)


def print_calendar_export_instructions() -> None:
    """Print instructions for exporting a calendar file."""
    print("\nPlease export your calendar from the Calendar app:")
    print("1. Open the Calendar app")
    print("2. Select the calendar(s) you want to analyze")
    print("3. Go to File > Export")
    print("4. Save the calendar file")
    print("\nThen run this script with the path to your exported file:")
    print("uv run calendar-analyzer --calendar /path/to/your/calendar.ics")


def get_calendar_path(calendar_file: str | Path | None = None) -> Path:
    """Get the requested calendar path or discover the newest local calendar file."""
    if calendar_file is not None:
        return _resolve_requested_calendar_path(calendar_file)

    calendar_files = _discover_calendar_files(_candidate_calendar_directories(Path.home()))
    if not calendar_files:
        print("\nError: No calendar files found in any of the expected locations.")
        print_calendar_export_instructions()
        raise_system_exit()

    latest_calendar = max(calendar_files, key=lambda path: (path.stat().st_mtime, path.as_posix()))
    print(f"\nSelected most recent calendar file: {latest_calendar}")
    return latest_calendar


def analyze_calendar(
    calendar_path: Path,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    days_back: int = DEFAULT_DAYS_BACK,
) -> CalendarAnalysis:
    """Analyze calendar events from the specified date range."""
    if calendar_path.suffix.lower() == ".sqlitedb":
        return analyze_sqlite_calendar(calendar_path, start_date, end_date)
    if calendar_path.suffix.lower() == ".icbu":
        sqlite_db_path = calendar_path / "Calendar.sqlitedb"
        if sqlite_db_path.exists():
            print(f"Found SQLite database in ICBU backup: {sqlite_db_path}")
            return analyze_sqlite_calendar(sqlite_db_path, start_date, end_date)

    ics_path = _resolve_ics_calendar_path(calendar_path)
    start_date, end_date = _resolve_date_range(start_date, end_date, days_back)

    try:
        calendar = _read_ics_calendar(ics_path)
    except OSError as error:
        print(f"Error reading calendar file: {error}")
        raise_system_exit()

    return _analyze_ics_events(calendar, start_date, end_date)


def analyze_sqlite_calendar(
    calendar_path: Path,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> CalendarAnalysis:
    """Analyze calendar events from a SQLite database."""
    start_date, end_date = _resolve_date_range(start_date, end_date, DEFAULT_DAYS_BACK)
    start_seconds = int((start_date - APPLE_EPOCH).total_seconds())
    end_seconds = int((end_date - APPLE_EPOCH).total_seconds())

    try:
        rows = _fetch_sqlite_calendar_rows(calendar_path, start_seconds, end_seconds)
    except sqlite3.Error as error:
        print(f"Error reading SQLite calendar: {error}")
        raise_system_exit()

    return _analyze_sqlite_rows(rows)


def generate_summary(meetings: list[Meeting], stats: MeetingStats, num_titles: int = 50) -> str:
    """Generate a summary of the calendar analysis."""
    if not meetings:
        return "No meetings found in the specified time period."

    df = pd.DataFrame(meetings)
    start_date = df["date"].min()
    end_date = df["date"].max()
    date_range_days = max((end_date - start_date).days, 1)
    avg_meetings_per_day = stats["total_meetings"] / date_range_days
    avg_meeting_duration = stats["total_hours"] / stats["total_meetings"]

    now = datetime.now(PACIFIC)
    is_dst = now.dst() != timedelta(0)
    timezone_name = "PDT" if is_dst else "PST"

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

    time_counts = df["time"].value_counts().head()
    summary.extend(
        f"- {meeting_time.strftime('%I:%M %p')}: {count} meetings" for meeting_time, count in time_counts.items()
    )
    summary.extend([f"\nTop {num_titles} Most Frequent Meeting Titles:", "-" * 30])

    title_counts = df["summary"].value_counts().head(num_titles)
    for title, count in title_counts.items():
        display_title = f"{title[:100]}..." if len(title) > 100 else title
        summary.append(f"{count:4d} | {display_title}")

    return "\n".join(summary)


def main() -> None:
    """Run the calendar analyzer command-line interface."""
    args = _parse_args()
    start_date = _parse_date_argument(args.start_date, "Start")
    end_date = _parse_date_argument(args.end_date, "End")
    _validate_date_range(start_date, end_date)

    print("📊 Analyzing your calendar...")
    calendar_path = get_calendar_path(args.calendar)
    print(f"Found calendar at: {calendar_path}")

    meetings, stats = analyze_calendar(calendar_path, start_date, end_date, args.days)
    summary = generate_summary(meetings, stats, args.titles)
    _write_or_print_summary(summary, args.output)


def raise_system_exit() -> NoReturn:
    """Exit with the conventional CLI failure status."""
    sys.exit(1)


def _resolve_requested_calendar_path(calendar_file: str | Path) -> Path:
    try:
        calendar_path = Path(calendar_file).resolve()
    except OSError as error:
        print(f"Error processing path: {error}")
        raise_system_exit()

    print(f"Looking for calendar at: {calendar_path}")
    print(f"Path exists: {calendar_path.exists()}")
    if calendar_path.exists():
        print(f"Is directory: {calendar_path.is_dir()}")
        if calendar_path.is_dir():
            print("Directory contents:")
            for item in calendar_path.iterdir():
                print(f"  - {item.name}")

    return calendar_path


def _candidate_calendar_directories(home: Path) -> list[Path]:
    return [
        home / "Library/Calendars",
        home / "Library/Application Support/Calendar",
        home / "Library/Application Support/Apple/Calendar",
        home / "Documents",
        home / "Downloads",
    ]


def _discover_calendar_files(paths: Iterable[Path]) -> list[Path]:
    print("\nSearching for calendar files in:")
    calendar_files: list[Path] = []
    for path in paths:
        files = _calendar_files_in_directory(path)
        calendar_files.extend(files)
        _print_directory_search_result(path, files)
    return calendar_files


def _calendar_files_in_directory(path: Path) -> list[Path]:
    if not path.exists():
        return []

    calendar_files: list[Path] = []
    for pattern in CALENDAR_PATTERNS:
        calendar_files.extend(sorted(path.rglob(pattern)))
    return calendar_files


def _print_directory_search_result(path: Path, calendar_files: list[Path]) -> None:
    print(f"- {path}")
    if not path.exists():
        print("  ✗ Directory does not exist")
        return

    print("  ✓ Directory exists")
    if not calendar_files:
        print("  ✗ No calendar files found")
        return

    print(f"  ✓ Found {len(calendar_files)} calendar files")
    for calendar_file in calendar_files[:5]:
        print(f"    - {calendar_file}")
    if len(calendar_files) > 5:
        print(f"    ... and {len(calendar_files) - 5} more")


def _resolve_ics_calendar_path(calendar_path: Path) -> Path:
    if calendar_path.suffix.lower() != ".icbu":
        return calendar_path

    if ics_files := sorted(calendar_path.glob("*.ics")):
        ics_path = ics_files[0]
        print(f"Found ICS file in ICBU backup: {ics_path}")
        return ics_path

    print(f"Error: Could not find calendar data (SQLite or ICS) in {calendar_path}")
    _print_directory_contents(calendar_path, "Contents of ICBU directory:")
    raise_system_exit()


def _print_directory_contents(directory: Path, heading: str) -> None:
    print(heading)
    try:
        for item in directory.iterdir():
            print(f"  - {item.name}")
    except OSError as error:
        print(f"  Error listing directory contents: {error}")


def _resolve_date_range(
    start_date: datetime | None,
    end_date: datetime | None,
    days_back: int,
) -> tuple[datetime, datetime]:
    resolved_end_date = end_date or datetime.now(PACIFIC)
    resolved_start_date = start_date or resolved_end_date - timedelta(days=days_back)
    return resolved_start_date, resolved_end_date


def _read_ics_calendar(calendar_path: Path) -> Calendar:
    with calendar_path.open("rb") as calendar_file:
        return Calendar.from_ical(calendar_file.read())


def _analyze_ics_events(calendar: Calendar, start_date: datetime, end_date: datetime) -> CalendarAnalysis:
    meetings: list[Meeting] = []
    stats = _empty_stats()

    for event in calendar.walk("VEVENT"):
        start = _event_start_datetime(event)
        if start is None:
            continue

        start = convert_to_pacific(start)
        if not start_date <= start <= end_date:
            continue

        duration_hours = _event_duration_hours(event, start)
        meeting = _meeting_from_event(event, start, duration_hours)
        meetings.append(meeting)
        _update_stats(stats, duration_hours)

    return meetings, stats


def _event_start_datetime(event: Any) -> datetime | None:
    start = event.get("dtstart")
    if start is None or not isinstance(start.dt, datetime):
        return None
    return start.dt


def _event_duration_hours(event: Any, start: datetime) -> float:
    duration = event.get("duration")
    if isinstance(duration, timedelta):
        return duration.total_seconds() / 3600

    duration_dt = getattr(duration, "dt", None)
    if isinstance(duration_dt, timedelta):
        return duration_dt.total_seconds() / 3600

    if duration is not None:
        return _duration_string_hours(str(duration))

    return _dtend_duration_hours(event, start) or DEFAULT_DURATION_HOURS


def _duration_string_hours(duration: str) -> float:
    if duration.startswith("PT") and duration.endswith("H"):
        try:
            return float(duration[2:-1])
        except ValueError:
            return DEFAULT_DURATION_HOURS
    return DEFAULT_DURATION_HOURS


def _dtend_duration_hours(event: Any, start: datetime) -> float | None:
    end = event.get("dtend")
    if end is None or not isinstance(end.dt, datetime):
        return None

    end_dt = convert_to_pacific(end.dt)
    return (end_dt - start).total_seconds() / 3600


def _meeting_from_event(event: Any, start: datetime, duration_hours: float) -> Meeting:
    return {
        "date": start.date(),
        "time": start.time(),
        "summary": str(event.get("summary", "No Title")),
        "duration_hours": duration_hours,
    }


def _fetch_sqlite_calendar_rows(
    calendar_path: Path,
    start_seconds: int,
    end_seconds: int,
) -> list[tuple[str | None, int, int]]:
    with closing(sqlite3.connect(calendar_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                summary,
                start_date,
                end_date
            FROM CalendarItem
            WHERE start_date >= ? AND start_date <= ?
            """,
            (start_seconds, end_seconds),
        )
        return cursor.fetchall()


def _analyze_sqlite_rows(rows: Iterable[tuple[str | None, int, int]]) -> CalendarAnalysis:
    meetings: list[Meeting] = []
    stats = _empty_stats()

    for summary, start_seconds, end_seconds in rows:
        start_dt = convert_to_pacific(APPLE_EPOCH + timedelta(seconds=start_seconds))
        end_dt = convert_to_pacific(APPLE_EPOCH + timedelta(seconds=end_seconds))
        duration_hours = (end_dt - start_dt).total_seconds() / 3600
        meetings.append(
            {
                "date": start_dt.date(),
                "time": start_dt.time(),
                "summary": summary or "No Title",
                "duration_hours": duration_hours,
            }
        )
        _update_stats(stats, duration_hours)

    return meetings, stats


def _empty_stats() -> MeetingStats:
    return {"total_meetings": 0, "total_hours": 0.0}


def _update_stats(stats: MeetingStats, duration_hours: float) -> None:
    stats["total_meetings"] += 1
    stats["total_hours"] += duration_hours


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze calendar events from a specified date range.")
    parser.add_argument("--calendar", help="Path to the exported calendar file (.ics)")
    parser.add_argument("--start-date", help="Start date for analysis (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date for analysis (YYYY-MM-DD)")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help=f"Number of days to look back from end date (default: {DEFAULT_DAYS_BACK})",
    )
    parser.add_argument("--titles", type=int, default=50, help="Number of meeting titles to display (default: 50)")
    parser.add_argument("--output", help="Path to save the analysis summary (default: print to console)")
    return parser.parse_args()


def _parse_date_argument(value: str | None, label: str) -> datetime | None:
    if value is None:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=PACIFIC)
    except ValueError:
        print(f"Error: {label} date must be in YYYY-MM-DD format")
        raise_system_exit()


def _validate_date_range(start_date: datetime | None, end_date: datetime | None) -> None:
    if start_date is None or end_date is None or end_date >= start_date:
        return

    print("Error: End date cannot be before start date")
    print(f"Start date: {start_date.strftime('%Y-%m-%d')}")
    print(f"End date: {end_date.strftime('%Y-%m-%d')}")
    raise_system_exit()


def _write_or_print_summary(summary: str, output: str | None) -> None:
    if output is None:
        print("\n" + summary)
        return

    try:
        Path(output).write_text(summary, encoding="utf-8")
    except OSError as error:
        print(f"Error saving to file: {error}")
        raise_system_exit()

    print(f"\nAnalysis saved to: {output}")


if __name__ == "__main__":
    main()
