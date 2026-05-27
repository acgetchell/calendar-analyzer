"""Analyze Apple and Outlook calendar exports and summarize meeting patterns."""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TypedDict
from zoneinfo import ZoneInfo

import pandas as pd
from defusedxml import ElementTree
from icalendar import Calendar

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable
    from xml.etree.ElementTree import Element

PACIFIC = ZoneInfo("America/Los_Angeles")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
CALENDAR_PATTERNS = ("*.ics", "*.icbu", "*.sqlitedb", "*.olm")
DEFAULT_DAYS_BACK = 365
DEFAULT_DURATION_HOURS = 1.0
MAX_TIMED_MEETING_HOURS = 8.0
OUTLOOK_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y")
OUTLOOK_TIME_FORMATS = ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S")
OUTLOOK_DATETIME_FORMATS = (
    *(f"{date_format} {time_format}" for date_format in OUTLOOK_DATE_FORMATS for time_format in OUTLOOK_TIME_FORMATS),
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


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


@dataclass(frozen=True)
class SummaryOptions:
    """Display options for the generated calendar summary."""

    num_titles: int = 50
    num_times: int = 5
    period_start: datetime | None = None
    period_end: datetime | None = None


CalendarAnalysis = tuple[list[Meeting], MeetingStats]
SqliteCalendarRow = tuple[str | None, int, int, Any]


def convert_to_pacific(dt: datetime) -> datetime:
    """Convert a datetime to Pacific time, treating naive values as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(PACIFIC)


def print_calendar_export_instructions() -> None:
    """Print instructions for exporting a calendar file."""
    print("\nPlease export your calendar from Calendar or Outlook:")
    print("1. Apple Calendar: select the calendar, then use File > Export")
    print("2. Outlook for Mac: export an Outlook archive (.olm)")
    print("3. Outlook can also be analyzed from exported ICS or CSV calendar files")
    print("4. Save the calendar file")
    print("\nThen run this script with the path to your exported file:")
    print("just run --calendar /path/to/your/calendar.olm")


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
        return analyze_sqlite_calendar(calendar_path, start_date, end_date, days_back)
    if calendar_path.suffix.lower() == ".olm":
        return analyze_olm_calendar(calendar_path, start_date, end_date, days_back)
    if calendar_path.suffix.lower() == ".csv":
        return analyze_outlook_csv_calendar(calendar_path, start_date, end_date, days_back)
    if calendar_path.suffix.lower() == ".icbu":
        sqlite_db_path = calendar_path / "Calendar.sqlitedb"
        if sqlite_db_path.exists():
            print(f"Found SQLite database in ICBU backup: {sqlite_db_path}")
            return analyze_sqlite_calendar(sqlite_db_path, start_date, end_date, days_back)

    ics_path = _resolve_ics_calendar_path(calendar_path)
    start_date, end_date = _resolve_date_range(start_date, end_date, days_back)

    try:
        calendar = _read_ics_calendar(ics_path)
    except OSError as error:
        print(f"Error reading calendar file: {error}")
        raise_system_exit()
    except ValueError as error:
        print(f"Error parsing calendar file: {error}")
        raise_system_exit()

    return _analyze_ics_events(calendar, start_date, end_date)


def analyze_sqlite_calendar(
    calendar_path: Path,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    days_back: int = DEFAULT_DAYS_BACK,
) -> CalendarAnalysis:
    """Analyze calendar events from a SQLite database."""
    start_date, end_date = _resolve_date_range(start_date, end_date, days_back)
    start_seconds = int((start_date - APPLE_EPOCH).total_seconds())
    end_seconds = int((end_date - APPLE_EPOCH).total_seconds())

    try:
        rows = _fetch_sqlite_calendar_rows(calendar_path, start_seconds, end_seconds)
    except sqlite3.Error as error:
        print(f"Error reading SQLite calendar: {error}")
        raise_system_exit()

    return _analyze_sqlite_rows(rows)


def analyze_outlook_csv_calendar(
    calendar_path: Path,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    days_back: int = DEFAULT_DAYS_BACK,
) -> CalendarAnalysis:
    """Analyze events from an Outlook CSV calendar export."""
    start_date, end_date = _resolve_date_range(start_date, end_date, days_back)

    try:
        rows = _read_outlook_csv_rows(calendar_path)
    except OSError as error:
        print(f"Error reading Outlook CSV calendar: {error}")
        raise_system_exit()
    except ValueError as error:
        print(f"Error parsing Outlook CSV calendar: {error}")
        raise_system_exit()

    return _analyze_outlook_csv_rows(rows, start_date, end_date)


def analyze_olm_calendar(
    calendar_path: Path,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    days_back: int = DEFAULT_DAYS_BACK,
) -> CalendarAnalysis:
    """Analyze calendar appointments from an Outlook for Mac OLM archive."""
    start_date, end_date = _resolve_date_range(start_date, end_date, days_back)

    try:
        appointments = _read_olm_appointments(calendar_path)
        return _analyze_olm_appointments(appointments, start_date, end_date)
    except (OSError, zipfile.BadZipFile) as error:
        print(f"Error reading OLM calendar: {error}")
        raise_system_exit()
    except ValueError as error:
        print(f"Error parsing OLM calendar: {error}")
        raise_system_exit()


def generate_summary(
    meetings: list[Meeting],
    stats: MeetingStats,
    options: SummaryOptions | None = None,
) -> str:
    """Generate a summary of the calendar analysis."""
    if not meetings:
        return "No meetings found in the specified time period."

    options = options or SummaryOptions()
    df = pd.DataFrame(meetings)
    start_date = options.period_start.date() if options.period_start is not None else df["date"].min()
    end_date = options.period_end.date() if options.period_end is not None else df["date"].max()
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
        f"\nTop {options.num_times} Most Common Meeting Times ({timezone_name}):",
    ]

    time_counts = df["time"].value_counts().head(options.num_times)
    summary.extend(
        f"- {meeting_time.strftime('%I:%M %p')}: {count} meetings" for meeting_time, count in time_counts.items()
    )
    summary.extend([f"\nTop {options.num_titles} Most Frequent Meeting Titles:", "-" * 30])

    title_counts = df["summary"].value_counts().head(options.num_titles)
    for title, count in title_counts.items():
        display_title = f"{title[:100]}..." if len(title) > 100 else title
        summary.append(f"{count:4d} | {display_title}")

    return "\n".join(summary)


def main() -> None:
    """Run the calendar analyzer command-line interface."""
    args = _parse_args()
    requested_start_date = _parse_date_argument(args.start_date, "Start")
    requested_end_date = _parse_date_argument(args.end_date, "End")
    _validate_date_range(requested_start_date, requested_end_date)
    excluded_title_patterns = _compile_title_exclusion_patterns(args.exclude_titles)
    start_date, end_date = _resolve_date_range(requested_start_date, requested_end_date, args.days)

    print("📊 Analyzing your calendar...")
    calendar_path = get_calendar_path(args.calendar)
    print(f"Found calendar at: {calendar_path}")

    meetings, stats = analyze_calendar(calendar_path, start_date, end_date, args.days)
    meetings, stats = _exclude_title_matches(meetings, excluded_title_patterns)
    summary = generate_summary(
        meetings,
        stats,
        SummaryOptions(
            num_titles=args.titles,
            num_times=args.times,
            period_start=start_date,
            period_end=end_date,
        ),
    )
    _write_or_print_summary(summary, args.output)


def raise_system_exit() -> NoReturn:
    """Exit with the conventional CLI failure status."""
    sys.exit(1)


def _resolve_requested_calendar_path(calendar_file: str | Path) -> Path:
    """Resolve an explicitly supplied calendar path and print discovery details."""
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
    """Return default directories searched for calendar exports."""
    return [
        home / "Library/Calendars",
        home / "Library/Application Support/Calendar",
        home / "Library/Application Support/Apple/Calendar",
        home / "Documents",
        home / "Downloads",
    ]


def _discover_calendar_files(paths: Iterable[Path]) -> list[Path]:
    """Search candidate directories for supported calendar files."""
    print("\nSearching for calendar files in:")
    calendar_files: list[Path] = []
    for path in paths:
        files = _calendar_files_in_directory(path)
        calendar_files.extend(files)
        _print_directory_search_result(path, files)
    return calendar_files


def _calendar_files_in_directory(path: Path) -> list[Path]:
    """Return supported calendar files found recursively in a directory."""
    if not path.exists():
        return []

    calendar_files: list[Path] = []
    for pattern in CALENDAR_PATTERNS:
        calendar_files.extend(sorted(path.rglob(pattern)))
    return calendar_files


def _print_directory_search_result(path: Path, calendar_files: list[Path]) -> None:
    """Print the calendar discovery result for a searched directory."""
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
    """Return the ICS path to analyze, including the first ICS inside an ICBU backup."""
    if calendar_path.suffix.lower() != ".icbu":
        return calendar_path

    if ics_files := sorted(calendar_path.glob("*.ics")):
        ics_path = ics_files[0]
        print(f"Found ICS file in ICBU backup: {ics_path}")
        return ics_path

    print(f"Error: Could not find calendar data (SQLite or ICS) in {calendar_path}")
    _print_directory_contents(calendar_path, "Contents of ICBU directory:")
    return raise_system_exit()


def _print_directory_contents(directory: Path, heading: str) -> None:
    """Print directory contents for diagnostics without failing on listing errors."""
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
    """Resolve optional date bounds into an inclusive analysis window."""
    resolved_end_date = end_date or datetime.now(PACIFIC)
    resolved_start_date = start_date or resolved_end_date - timedelta(days=days_back)
    return resolved_start_date, resolved_end_date


def _read_ics_calendar(calendar_path: Path) -> Calendar:
    """Read and parse an ICS calendar file."""
    with calendar_path.open("rb") as calendar_file:
        return Calendar.from_ical(calendar_file.read())


def _analyze_ics_events(calendar: Calendar, start_date: datetime, end_date: datetime) -> CalendarAnalysis:
    """Collect meetings and aggregate stats from VEVENT entries in a date range."""
    meetings: list[Meeting] = []
    stats = _empty_stats()

    for event in calendar.walk("VEVENT"):
        if _event_is_transparent(event):
            continue

        raw_start = _event_start_datetime(event)
        if raw_start is None:
            continue

        start = convert_to_pacific(raw_start)
        if not start_date <= start <= end_date:
            continue

        duration_hours = _event_duration_hours(event, start)
        if _is_all_day_like_calendar_block(start, duration_hours) or _is_floating_midnight(raw_start):
            continue

        meeting = _meeting_from_event(event, start, duration_hours)
        meetings.append(meeting)
        _update_stats(stats, duration_hours)

    return meetings, stats


def _event_start_datetime(event: Any) -> datetime | None:
    """Return a VEVENT start datetime when the event has one."""
    start = event.get("dtstart")
    if start is None or not isinstance(start.dt, datetime):
        return None
    return start.dt


def _event_is_transparent(event: Any) -> bool:
    """Return whether a VEVENT is marked free/transparent."""
    transparency = event.get("transp")
    return str(transparency or "").strip().upper() == "TRANSPARENT"


def _event_duration_hours(event: Any, start: datetime) -> float:
    """Resolve a VEVENT duration in hours from duration, dtend, or a default."""
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
    """Parse simple ICS duration strings like PT1H into hours."""
    if duration.startswith("PT") and duration.endswith("H"):
        try:
            return float(duration[2:-1])
        except ValueError:
            return DEFAULT_DURATION_HOURS
    return DEFAULT_DURATION_HOURS


def _dtend_duration_hours(event: Any, start: datetime) -> float | None:
    """Calculate event duration from dtend when it is available."""
    end = event.get("dtend")
    if end is None or not isinstance(end.dt, datetime):
        return None

    end_dt = convert_to_pacific(end.dt)
    return (end_dt - start).total_seconds() / 3600


def _meeting_from_event(event: Any, start: datetime, duration_hours: float) -> Meeting:
    """Normalize a VEVENT into the meeting shape used by reporting."""
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
) -> list[SqliteCalendarRow]:
    """Fetch SQLite calendar rows in the Apple-epoch date range."""
    with closing(sqlite3.connect(calendar_path)) as conn:
        cursor = conn.cursor()
        all_day_column = _sqlite_all_day_column(cursor)
        all_day_expression = _quote_sqlite_identifier(all_day_column) if all_day_column is not None else "0"
        # The only interpolated SQL is a column name returned by PRAGMA table_info.
        query = f"""
            SELECT
                summary,
                start_date,
                end_date,
                {all_day_expression}
            FROM CalendarItem
            WHERE start_date >= ? AND start_date <= ?
            """  # noqa: S608
        cursor.execute(query, (start_seconds, end_seconds))
        return cursor.fetchall()


def _sqlite_all_day_column(cursor: sqlite3.Cursor) -> str | None:
    """Return the CalendarItem all-day column name when the schema has one."""
    cursor.execute("PRAGMA table_info(CalendarItem)")
    for column in cursor.fetchall():
        column_name = str(column[1])
        if _normalize_csv_field(column_name) in {"allday", "isallday"}:
            return column_name
    return None


def _quote_sqlite_identifier(identifier: str) -> str:
    """Quote a SQLite identifier that came from SQLite schema introspection."""
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _analyze_sqlite_rows(rows: Iterable[SqliteCalendarRow]) -> CalendarAnalysis:
    """Normalize SQLite calendar rows and aggregate meeting stats."""
    meetings: list[Meeting] = []
    stats = _empty_stats()

    for summary, start_seconds, end_seconds, is_all_day in rows:
        if _csv_truthy(str(is_all_day)):
            continue

        start_dt = convert_to_pacific(APPLE_EPOCH + timedelta(seconds=start_seconds))
        end_dt = convert_to_pacific(APPLE_EPOCH + timedelta(seconds=end_seconds))
        duration_hours = _positive_duration_hours(start_dt, end_dt)
        if _is_all_day_like_calendar_block(start_dt, duration_hours):
            continue

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


def _read_olm_appointments(calendar_path: Path) -> list[Element]:
    """Read appointment XML entries from an Outlook for Mac OLM archive."""
    appointments: list[Element] = []
    calendar_entries = 0
    with zipfile.ZipFile(calendar_path) as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith("Calendar.xml"):
                continue

            calendar_entries += 1
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError as error:
                msg = f"{name} is not valid XML: {error}"
                raise ValueError(msg) from error
            appointments.extend(_olm_xml_appointments(root))

    if calendar_entries == 0:
        msg = "No Calendar.xml entries found in OLM archive."
        raise ValueError(msg)
    return appointments


def _olm_xml_appointments(root: Element) -> list[Element]:
    """Return appointment elements from an OLM Calendar.xml tree."""
    return [element for element in root.iter() if _xml_local_name(element.tag) == "appointment"]


def _analyze_olm_appointments(
    appointments: Iterable[Element],
    start_date: datetime,
    end_date: datetime,
) -> CalendarAnalysis:
    """Normalize OLM appointment XML and aggregate meeting stats."""
    meetings: list[Meeting] = []
    stats = _empty_stats()
    dated_appointments = 0
    unparsable_start_values: list[str] = []

    for appointment in appointments:
        if _olm_appointment_is_all_day(appointment) or _olm_appointment_is_free(appointment):
            continue

        start_value = _olm_appointment_start_text(appointment)
        if start_value is not None:
            dated_appointments += 1

        start = _olm_appointment_start(appointment)
        if start is None:
            if start_value is not None:
                unparsable_start_values.append(start_value)
            continue

        start = convert_to_pacific(start)
        if not start_date <= start <= end_date:
            continue

        end = _olm_appointment_end(appointment, start)
        duration_hours = _positive_duration_hours(start, end)
        if _is_all_day_like_calendar_block(start, duration_hours):
            continue

        meetings.append(
            {
                "date": start.date(),
                "time": start.time(),
                "summary": _xml_text(appointment, ("OPFCalendarEventCopySummary", "Subject", "Summary")) or "No Title",
                "duration_hours": duration_hours,
            }
        )
        _update_stats(stats, duration_hours)

    if dated_appointments and dated_appointments == len(unparsable_start_values):
        examples = ", ".join(unparsable_start_values[:3])
        msg = f"Could not parse any OLM appointment start dates. Example value(s): {examples}"
        raise ValueError(msg)

    return meetings, stats


def _olm_appointment_start(appointment: Element) -> datetime | None:
    """Return the start datetime from an OLM appointment."""
    start = _outlook_datetime(_olm_appointment_start_text(appointment), require_time=True)
    return start or _outlook_xml_split_datetime(
        appointment,
        ("OPFCalendarEventCopyStartDate", "StartDate"),
        ("OPFCalendarEventCopyStartTime", "StartTime"),
    )


def _olm_appointment_start_text(appointment: Element) -> str | None:
    """Return the raw OLM appointment start value."""
    return _xml_text(
        appointment,
        (
            "OPFCalendarEventCopyStartTime",
            "StartTime",
        ),
    )


def _olm_appointment_is_all_day(appointment: Element) -> bool:
    """Return whether an OLM appointment is explicitly or implicitly all-day."""
    if _xml_truthy(
        _xml_text(
            appointment,
            (
                "OPFCalendarEventGetIsAllDayEvent",
                "OPFCalendarEventCopyIsAllDayEvent",
                "OPFCalendarEventCopyAllDayEvent",
                "AllDayEvent",
                "AllDay",
            ),
        )
    ):
        return True

    return _olm_appointment_start_text(appointment) is None and _olm_appointment_date_text(appointment) is not None


def _olm_appointment_is_free(appointment: Element) -> bool:
    """Return whether an OLM appointment is marked free on the calendar."""
    return _is_free_busy_free(_xml_text(appointment, ("OPFCalendarEventCopyFreeBusyStatus", "FreeBusyStatus")))


def _olm_appointment_date_text(appointment: Element) -> str | None:
    """Return the raw OLM appointment date-only value."""
    return _xml_text(appointment, ("OPFCalendarEventCopyStartDate", "StartDate"))


def _olm_appointment_end(appointment: Element, start: datetime) -> datetime:
    """Return the end datetime from an OLM appointment."""
    end = _outlook_datetime(_olm_appointment_end_text(appointment)) or _outlook_xml_split_datetime(
        appointment,
        ("OPFCalendarEventCopyEndDate", "EndDate"),
        ("OPFCalendarEventCopyEndTime", "EndTime"),
        fallback_date=start,
    )
    return end or start + timedelta(hours=DEFAULT_DURATION_HOURS)


def _olm_appointment_end_text(appointment: Element) -> str | None:
    """Return the raw OLM appointment end value."""
    return _xml_text(appointment, ("OPFCalendarEventCopyEndTime", "EndTime"))


def _xml_text(element: Element, field_names: tuple[str, ...]) -> str | None:
    """Return text from the first matching descendant XML element."""
    normalized_names = {_normalize_csv_field(field_name) for field_name in field_names}
    for child in element.iter():
        if _normalize_csv_field(_xml_local_name(child.tag)) not in normalized_names:
            continue

        text = "".join(child.itertext()).strip()
        if text:
            return text
    return None


def _xml_local_name(tag: str) -> str:
    """Return an XML tag name without any namespace prefix."""
    return tag.rsplit("}", maxsplit=1)[-1]


def _xml_truthy(value: str | None) -> bool:
    """Return whether an OLM XML boolean-ish value means true."""
    return value is not None and value.strip().lower() in {"1", "true", "yes", "y"}


def _read_outlook_csv_rows(calendar_path: Path) -> list[dict[str, str]]:
    """Read an Outlook CSV export and validate that it has usable date columns."""
    with calendar_path.open(newline="", encoding="utf-8-sig") as calendar_file:
        reader = csv.DictReader(calendar_file)
        fieldnames = reader.fieldnames or []
        _validate_outlook_csv_headers(fieldnames)
        return [{key: value or "" for key, value in row.items() if key is not None} for row in reader]


def _validate_outlook_csv_headers(fieldnames: Iterable[str]) -> None:
    """Raise a user-facing error when a CSV file does not look like a calendar export."""
    fields = list(fieldnames)
    if not fields:
        msg = "CSV header row is missing."
        raise ValueError(msg)

    has_split_start = _csv_has_field(fields, ("Start Date", "StartDate")) and _csv_has_field(
        fields,
        ("Start Time", "StartTime"),
    )
    has_combined_start = _csv_has_field(fields, ("Start", "Starts", "Start Date Time", "StartDateTime"))
    if not has_split_start and not has_combined_start:
        msg = "CSV must include Outlook start columns such as Start Date and Start Time, or a combined Start column."
        raise ValueError(msg)


def _analyze_outlook_csv_rows(
    rows: Iterable[dict[str, str]],
    start_date: datetime,
    end_date: datetime,
) -> CalendarAnalysis:
    """Normalize Outlook CSV rows and aggregate meeting stats."""
    meetings: list[Meeting] = []
    stats = _empty_stats()

    for row in rows:
        start = _outlook_csv_start_datetime(row)
        if start is None:
            continue

        start = convert_to_pacific(start)
        if not start_date <= start <= end_date:
            continue

        end = _outlook_csv_end_datetime(row, start)
        duration_hours = _positive_duration_hours(start, end)
        if _is_all_day_like_calendar_block(start, duration_hours):
            continue

        meetings.append(
            {
                "date": start.date(),
                "time": start.time(),
                "summary": _csv_value(row, ("Subject", "Title", "Summary")) or "No Title",
                "duration_hours": duration_hours,
            }
        )
        _update_stats(stats, duration_hours)

    return meetings, stats


def _outlook_csv_start_datetime(row: dict[str, str]) -> datetime | None:
    """Return the row start datetime, skipping all-day rows."""
    if _csv_truthy(_csv_value(row, ("All day event", "All Day Event", "All Day", "AllDayEvent"))):
        return None
    if _is_free_busy_free(_csv_value(row, ("Show Time As", "Show As", "Busy Status", "FreeBusyStatus"))):
        return None

    return _outlook_split_datetime(row, ("Start Date", "StartDate"), ("Start Time", "StartTime")) or _outlook_datetime(
        _csv_value(row, ("Start", "Starts", "Start Date Time", "StartDateTime")),
        require_time=True,
    )


def _outlook_csv_end_datetime(row: dict[str, str], start: datetime) -> datetime:
    """Return the row end datetime, defaulting when Outlook omitted one."""
    end = _outlook_split_datetime(
        row,
        ("End Date", "EndDate"),
        ("End Time", "EndTime"),
        fallback_date=start,
    ) or _outlook_datetime(_csv_value(row, ("End", "Ends", "End Date Time", "EndDateTime")))

    return end or start + timedelta(hours=DEFAULT_DURATION_HOURS)


def _outlook_split_datetime(
    row: dict[str, str],
    date_aliases: tuple[str, ...],
    time_aliases: tuple[str, ...],
    fallback_date: datetime | None = None,
) -> datetime | None:
    """Parse Outlook date and time columns into a Pacific-aware datetime."""
    return _outlook_datetime_from_parts(_csv_value(row, date_aliases), _csv_value(row, time_aliases), fallback_date)


def _outlook_xml_split_datetime(
    element: Element,
    date_aliases: tuple[str, ...],
    time_aliases: tuple[str, ...],
    fallback_date: datetime | None = None,
) -> datetime | None:
    """Parse Outlook XML date and time fields into a Pacific-aware datetime."""
    return _outlook_datetime_from_parts(
        _xml_text(element, date_aliases),
        _xml_text(element, time_aliases),
        fallback_date,
    )


def _outlook_datetime_from_parts(
    date_value: str | None,
    time_value: str | None,
    fallback_date: datetime | None = None,
) -> datetime | None:
    """Parse date and time parts, treating missing start times as all-day."""
    if not date_value and fallback_date is None:
        return None
    if not time_value and fallback_date is None:
        return None
    has_explicit_time = bool(time_value)
    if not date_value:
        date_value = _fallback_date_text(fallback_date)
    if not time_value:
        time_value = "12:00 AM"

    parsed = _outlook_datetime(f"{date_value} {time_value}")
    if parsed is not None:
        return parsed
    if has_explicit_time:
        return None
    return _outlook_datetime(date_value)


def _fallback_date_text(fallback_date: datetime | None) -> str:
    """Return a date string for the already-validated fallback date."""
    if fallback_date is None:
        msg = "Fallback date is required."
        raise ValueError(msg)
    return fallback_date.strftime("%Y-%m-%d")


def _outlook_datetime(value: str | None, *, require_time: bool = False) -> datetime | None:
    """Parse a date/time value from an Outlook CSV export."""
    if value is None or not value.strip():
        return None

    text = value.strip()
    parsed = _parse_outlook_strptime(text, OUTLOOK_DATETIME_FORMATS)
    if parsed is not None:
        return parsed

    if not require_time:
        parsed = _parse_outlook_strptime(text, OUTLOOK_DATE_FORMATS)
        if parsed is not None:
            return parsed
    elif not _outlook_text_has_time(text):
        return None

    return _parse_outlook_iso_datetime(text)


def _parse_outlook_strptime(text: str, date_formats: tuple[str, ...]) -> datetime | None:
    """Parse an Outlook date/time string using known strptime formats."""
    for date_format in date_formats:
        try:
            return datetime.strptime(text, date_format).replace(tzinfo=PACIFIC)
        except ValueError:
            continue
    return None


def _parse_outlook_iso_datetime(text: str) -> datetime | None:
    """Parse an ISO datetime while preserving or assigning Pacific time."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=PACIFIC)
    return convert_to_pacific(parsed)


def _outlook_text_has_time(text: str) -> bool:
    """Return whether an Outlook date/time string appears to include a time."""
    return ":" in text or "T" in text


def _positive_duration_hours(start: datetime, end: datetime) -> float:
    """Return a positive duration, falling back when end precedes start."""
    duration_hours = (end - start).total_seconds() / 3600
    if duration_hours <= 0:
        return DEFAULT_DURATION_HOURS
    return duration_hours


def _is_all_day_like_calendar_block(start: datetime, duration_hours: float) -> bool:
    """Return whether a timed export row looks like an all-day/non-meeting block."""
    starts_at_midnight = _starts_at_midnight(start)
    return starts_at_midnight or duration_hours >= MAX_TIMED_MEETING_HOURS


def _is_floating_midnight(start: datetime) -> bool:
    """Return whether an ICS floating local start time is midnight."""
    return start.tzinfo is None and _starts_at_midnight(start)


def _starts_at_midnight(start: datetime) -> bool:
    """Return whether a datetime starts exactly at midnight."""
    return start.timetz().replace(tzinfo=None) == time()


def _csv_value(row: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    """Return a CSV value by case-insensitive, punctuation-insensitive field alias."""
    normalized_aliases = {_normalize_csv_field(alias) for alias in aliases}
    for field, raw_value in row.items():
        if _normalize_csv_field(field) in normalized_aliases:
            value = raw_value.strip()
            return value or None
    return None


def _csv_has_field(fields: Iterable[str], aliases: tuple[str, ...]) -> bool:
    """Return whether any CSV field matches one of the aliases."""
    normalized_aliases = {_normalize_csv_field(alias) for alias in aliases}
    return any(_normalize_csv_field(field) in normalized_aliases for field in fields)


def _normalize_csv_field(value: str) -> str:
    """Normalize CSV headers for tolerant matching across Outlook variants."""
    return "".join(character for character in value.lower() if character.isalnum())


def _csv_truthy(value: str | None) -> bool:
    """Return whether an Outlook CSV boolean-ish cell means true."""
    return value is not None and value.strip().lower() in {"1", "true", "yes", "y"}


def _is_free_busy_free(value: str | None) -> bool:
    """Return whether a free/busy value marks the item as free time."""
    return value is not None and value.strip().lower() in {"0", "free", "transparent"}


def _empty_stats() -> MeetingStats:
    """Create an empty meeting statistics accumulator."""
    return {"total_meetings": 0, "total_hours": 0.0}


def _compile_title_exclusion_patterns(patterns: list[str] | None) -> list[re.Pattern[str]]:
    """Compile case-insensitive title exclusion regexes."""
    compiled_patterns: list[re.Pattern[str]] = []
    for pattern in patterns or []:
        try:
            compiled_patterns.append(re.compile(pattern, re.IGNORECASE))
        except re.error as error:
            print(f"Error: Invalid --exclude-title regex {pattern!r}: {error}")
            raise_system_exit()
    return compiled_patterns


def _exclude_title_matches(
    meetings: list[Meeting],
    excluded_title_patterns: list[re.Pattern[str]],
) -> CalendarAnalysis:
    """Remove meetings whose titles match any excluded title regex."""
    if not excluded_title_patterns:
        return meetings, _stats_from_meetings(meetings)

    filtered_meetings = [
        meeting
        for meeting in meetings
        if not any(pattern.search(meeting["summary"]) for pattern in excluded_title_patterns)
    ]
    return filtered_meetings, _stats_from_meetings(filtered_meetings)


def _stats_from_meetings(meetings: list[Meeting]) -> MeetingStats:
    """Build aggregate statistics from normalized meetings."""
    return {
        "total_meetings": len(meetings),
        "total_hours": sum(meeting["duration_hours"] for meeting in meetings),
    }


def _update_stats(stats: MeetingStats, duration_hours: float) -> None:
    """Add one meeting and its duration to the statistics accumulator."""
    stats["total_meetings"] += 1
    stats["total_hours"] += duration_hours


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the calendar analyzer CLI."""
    parser = argparse.ArgumentParser(description="Analyze calendar events from a specified date range.")
    parser.add_argument("--calendar", help="Path to the exported calendar file (.ics, .olm, .csv, .icbu, .sqlitedb)")
    parser.add_argument("--start-date", help="Start date for analysis (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date for analysis (YYYY-MM-DD)")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help=f"Number of days to look back from end date (default: {DEFAULT_DAYS_BACK})",
    )
    parser.add_argument("--times", type=int, default=5, help="Number of meeting times to display (default: 5)")
    parser.add_argument("--titles", type=int, default=50, help="Number of meeting titles to display (default: 50)")
    parser.add_argument(
        "--exclude-title",
        action="append",
        dest="exclude_titles",
        help="Case-insensitive regex for meeting titles to exclude from statistics; may be repeated",
    )
    parser.add_argument("--output", help="Path to save the analysis summary (default: print to console)")
    return parser.parse_args()


def _parse_date_argument(value: str | None, label: str) -> datetime | None:
    """Parse an optional YYYY-MM-DD CLI date into a Pacific-aware datetime."""
    if value is None:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=PACIFIC)
    except ValueError:
        print(f"Error: {label} date must be in YYYY-MM-DD format")
        raise_system_exit()


def _validate_date_range(start_date: datetime | None, end_date: datetime | None) -> None:
    """Exit with a user-facing error if the date range is inverted."""
    if start_date is None or end_date is None or end_date >= start_date:
        return

    print("Error: End date cannot be before start date")
    print(f"Start date: {start_date.strftime('%Y-%m-%d')}")
    print(f"End date: {end_date.strftime('%Y-%m-%d')}")
    raise_system_exit()


def _write_or_print_summary(summary: str, output: str | None) -> None:
    """Write the summary to a file or print it when no output path is supplied."""
    if output is None:
        _write_summary_to_stdout(summary)
        return

    try:
        _write_text_atomic(Path(output), summary)
    except OSError as error:
        print(f"Error saving to file: {error}")
        raise_system_exit()

    print(f"\nAnalysis saved to: {output}")


def _write_summary_to_stdout(summary: str) -> None:
    """Write the requested meeting-title report to stdout."""
    sys.stdout.buffer.write(f"\n{summary}\n".encode())


def _write_text_atomic(output_path: Path, content: str) -> None:
    """Write text through a temporary file before replacing the destination."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=output_path.parent,
            encoding="utf-8",
            newline="\n",
        ) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        temp_path.replace(output_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


if __name__ == "__main__":  # pragma: no cover
    main()
