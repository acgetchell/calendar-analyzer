"""Analyze Apple and Outlook calendar exports and summarize meeting patterns."""

from __future__ import annotations

import argparse
import csv
import json
import os
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

import polars as pl
from defusedxml import ElementTree
from icalendar import Calendar

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable
    from xml.etree.ElementTree import Element

PACIFIC = ZoneInfo("America/Los_Angeles")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
CALENDAR_PATTERNS = ("*.ics", "*.icbu", "*.sqlitedb", "*.olm")
CACHE_SCHEMA_VERSION = 1
DEFAULT_DAYS_BACK = 365
DEFAULT_DURATION_HOURS = 1.0
MAX_TIMED_MEETING_HOURS = 8.0
UNSUPPORTED_CALENDAR_EXTENSIONS = {".pst"}
MEETING_FRAME_SCHEMA = pl.Schema(
    {
        "start": pl.Datetime("us"),
        "date": pl.Date,
        "time": pl.Time,
        "summary": pl.Utf8,
        "duration_hours": pl.Float64,
    }
)
OUTLOOK_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y")
OUTLOOK_TIME_FORMATS = ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S")
OUTLOOK_DATETIME_FORMATS = (
    *(f"{date_format} {time_format}" for date_format in OUTLOOK_DATE_FORMATS for time_format in OUTLOOK_TIME_FORMATS),
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


class Meeting(TypedDict):
    """Normalized calendar meeting data used for reporting."""

    start: datetime
    date: date
    time: time
    summary: str
    duration_hours: float


@dataclass(frozen=True)
class SummaryOptions:
    """Display options for the generated calendar summary."""

    num_titles: int = 50
    num_times: int = 5
    period_start: datetime | None = None
    period_end: datetime | None = None
    data_start: date | None = None
    data_end: date | None = None


SqliteCalendarRow = tuple[str | None, int, int, Any]


class SavedDataFrameReadError(RuntimeError):
    """Raised when a saved Polars DataFrame cannot be used for reporting."""


def convert_to_pacific(dt: datetime) -> datetime:
    """Convert a datetime to Pacific time, treating naive values as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(PACIFIC)


def print_calendar_export_instructions() -> None:
    """Print instructions for exporting a calendar file."""
    print("\nPlease export your calendar from Calendar or Outlook:")
    print("1. Apple Calendar: select the calendar, then use File > Export")
    print("2. Outlook for Mac: export an Outlook archive (.olm), or export an ICS calendar when available")
    print("3. Outlook for Windows: export a calendar-only ICS file with File > Save Calendar")
    print("4. Do not export a PST file; PST can include mail, contacts, tasks, and other mailbox data")
    print("5. Save the calendar file")
    print("\nThen run this script with the path to your exported file:")
    print("just run --calendar /path/to/your/calendar.ics")


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


def load_calendar_dataframe(
    calendar_file: str | Path | None,
    dataframe_path: Path,
    *,
    force_import: bool = False,
) -> pl.DataFrame:
    """Load saved calendar meetings or import and cache a calendar export."""
    if not force_import and _cached_dataframe_is_usable(dataframe_path, calendar_file):
        print(f"Found saved Polars DataFrame at: {dataframe_path}")
        try:
            frame = _read_meetings_dataframe(dataframe_path)
        except SavedDataFrameReadError as error:
            print(error)
            if calendar_file is None:
                raise_system_exit()
            print("Saved Polars DataFrame could not be read; importing calendar export instead.")
        else:
            _print_frame_coverage("Saved meeting data covers", frame)
            return frame

    if force_import:
        print("Forcing calendar import because --import was supplied.")
    elif dataframe_path.exists():
        print(f"Saved Polars DataFrame is stale or from another calendar: {dataframe_path}")
    else:
        print(f"No saved Polars DataFrame found at: {dataframe_path}")

    calendar_path = get_calendar_path(calendar_file)
    print(f"Found calendar at: {calendar_path}")
    frame = import_calendar_dataframe(calendar_path)
    _write_meetings_dataframe(frame, dataframe_path, calendar_path)
    print(f"Saved Polars DataFrame to: {dataframe_path}")
    _print_frame_coverage("Imported meeting data covers", frame)
    return frame


def analyze_calendar(
    calendar_path: Path,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    days_back: int = DEFAULT_DAYS_BACK,
) -> pl.DataFrame:
    """Analyze calendar events from the specified date range as a Polars DataFrame."""
    frame = import_calendar_dataframe(calendar_path)
    start_date, end_date = _resolve_date_range(start_date, end_date, days_back)
    return _filter_meetings_frame(frame, start_date, end_date)


def import_calendar_dataframe(calendar_path: Path) -> pl.DataFrame:
    """Import a calendar file into a normalized Polars DataFrame."""
    return _meetings_dataframe(import_calendar_meetings(calendar_path))


def import_calendar_meetings(calendar_path: Path) -> list[Meeting]:
    """Import normalized meetings from a supported calendar export."""
    suffix = calendar_path.suffix.lower()
    if suffix in UNSUPPORTED_CALENDAR_EXTENSIONS:
        print("Error: Outlook PST files are intentionally unsupported. Export a calendar-only .ics file instead.")
        raise_system_exit()
    if suffix == ".sqlitedb":
        return _import_sqlite_calendar_meetings(calendar_path)
    if suffix == ".olm":
        return _import_olm_calendar_meetings(calendar_path)
    if suffix == ".csv":
        return _import_outlook_csv_calendar_meetings(calendar_path)
    if suffix == ".icbu":
        sqlite_db_path = calendar_path / "Calendar.sqlitedb"
        if sqlite_db_path.exists():
            print(f"Found SQLite database in ICBU backup: {sqlite_db_path}")
            return _import_sqlite_calendar_meetings(sqlite_db_path)

    ics_path = _resolve_ics_calendar_path(calendar_path)
    return _import_ics_calendar_meetings(ics_path)


def _import_ics_calendar_meetings(calendar_path: Path) -> list[Meeting]:
    """Import normalized meetings from an ICS calendar file."""

    try:
        calendar = _read_ics_calendar(calendar_path)
    except OSError as error:
        print(f"Error reading calendar file: {error}")
        raise_system_exit()
    except ValueError as error:
        print(f"Error parsing calendar file: {error}")
        raise_system_exit()

    return _meetings_from_ics_events(calendar)


def _import_sqlite_calendar_meetings(calendar_path: Path) -> list[Meeting]:
    """Import normalized meetings from an Apple Calendar SQLite database."""

    try:
        rows = _fetch_sqlite_calendar_rows(calendar_path)
    except sqlite3.Error as error:
        print(f"Error reading SQLite calendar: {error}")
        raise_system_exit()

    return _meetings_from_sqlite_rows(rows)


def _import_outlook_csv_calendar_meetings(calendar_path: Path) -> list[Meeting]:
    """Import normalized meetings from an Outlook CSV calendar export."""

    try:
        rows = _read_outlook_csv_rows(calendar_path)
    except OSError as error:
        print(f"Error reading Outlook CSV calendar: {error}")
        raise_system_exit()
    except ValueError as error:
        print(f"Error parsing Outlook CSV calendar: {error}")
        raise_system_exit()

    return _meetings_from_outlook_csv_rows(rows)


def _import_olm_calendar_meetings(calendar_path: Path) -> list[Meeting]:
    """Import normalized meetings from an Outlook for Mac OLM archive."""

    try:
        appointments = _read_olm_appointments(calendar_path)
        return _meetings_from_olm_appointments(appointments)
    except (OSError, zipfile.BadZipFile) as error:
        print(f"Error reading OLM calendar: {error}")
        raise_system_exit()
    except ValueError as error:
        print(f"Error parsing OLM calendar: {error}")
        raise_system_exit()


def generate_summary(
    meetings: list[Meeting] | pl.DataFrame,
    options: SummaryOptions | None = None,
) -> str:
    """Generate a summary of the calendar analysis."""
    frame = _meetings_dataframe(meetings)
    options = options or SummaryOptions()
    if frame.is_empty():
        summary = ["No meetings found in the specified time period."]
        if options.data_start is not None or options.data_end is not None:
            summary.extend(
                [
                    "\nImported Data Coverage:",
                    f"- From: {_format_summary_date(options.data_start)}",
                    f"- To:   {_format_summary_date(options.data_end)}",
                ]
            )
        if options.period_start is not None or options.period_end is not None:
            query_start = options.period_start.date() if options.period_start is not None else None
            query_end = options.period_end.date() if options.period_end is not None else None
            summary.extend(
                [
                    "\nQuery Date Range:",
                    f"- From: {_format_summary_date(query_start)}",
                    f"- To:   {_format_summary_date(query_end)}",
                ]
            )
        return "\n".join(summary)

    query_start_date = (
        options.period_start.date() if options.period_start is not None else frame.select(pl.col("date").min()).item()
    )
    query_end_date = (
        options.period_end.date() if options.period_end is not None else frame.select(pl.col("date").max()).item()
    )
    data_start, data_end = _frame_date_bounds(frame)
    imported_start_date = options.data_start or data_start
    imported_end_date = options.data_end or data_end
    date_range_days = max((query_end_date - query_start_date).days + 1, 1)
    total_meetings = frame.height
    total_hours = frame.select(pl.col("duration_hours").sum()).item()
    avg_meetings_per_day = total_meetings / date_range_days
    avg_meeting_duration = total_hours / total_meetings

    now = datetime.now(PACIFIC)
    is_dst = now.dst() != timedelta(0)
    timezone_name = "PDT" if is_dst else "PST"

    summary = [
        f"📅 Calendar Analysis Summary (All times in Pacific Time - Currently {timezone_name})",
        "=" * 70,
        "\nImported Data Coverage:",
        f"- From: {_format_summary_date(imported_start_date)}",
        f"- To:   {_format_summary_date(imported_end_date)}",
        "\nQuery Date Range:",
        f"- From: {query_start_date.strftime('%B %d, %Y')}",
        f"- To:   {query_end_date.strftime('%B %d, %Y')}",
        f"- Span: {date_range_days} days",
        "\nTimezone Information:",
        f"- Currently using {timezone_name} (Pacific {'Daylight' if is_dst else 'Standard'} Time)",
        "- All times are automatically adjusted for DST transitions",
        "- Meetings during DST periods are shown in PDT",
        "- Meetings during standard time are shown in PST",
        "\nMeeting Statistics:",
        f"- Total Meetings: {total_meetings}",
        f"- Total Meeting Hours: {total_hours:.1f}",
        f"- Average Meetings per Day: {avg_meetings_per_day:.1f}",
        f"- Average Meeting Duration: {avg_meeting_duration:.1f} hours",
        f"\nTop {options.num_times} Most Common Meeting Times ({timezone_name}):",
    ]

    time_counts = (
        frame.group_by("time")
        .len(name="count")
        .sort(["count", "time"], descending=[True, False])
        .head(options.num_times)
    )
    summary.extend(
        f"- {row['time'].strftime('%I:%M %p')}: {row['count']} meetings" for row in time_counts.iter_rows(named=True)
    )
    summary.extend([f"\nTop {options.num_titles} Most Frequent Meeting Titles:", "-" * 30])

    title_counts = (
        frame.group_by("summary")
        .len(name="count")
        .sort(["count", "summary"], descending=[True, False])
        .head(options.num_titles)
    )
    for row in title_counts.iter_rows(named=True):
        title = row["summary"]
        count = row["count"]
        display_title = f"{title[:100]}..." if len(title) > 100 else title
        summary.append(f"{count:4d} | {display_title}")

    return "\n".join(summary)


def main() -> None:
    """Run the calendar analyzer command-line interface."""
    args = _parse_args()
    requested_start_date = _parse_date_argument(args.start_date, "Start")
    requested_end_date = _parse_date_argument(args.end_date, "End", end_of_day=True)
    _validate_date_range(requested_start_date, requested_end_date)
    excluded_title_patterns = _compile_title_exclusion_patterns(args.exclude_titles)
    start_date, end_date = _resolve_date_range(requested_start_date, requested_end_date, args.days)
    dataframe_path = _resolve_dataframe_path(args.dataframe, args.calendar)

    print("📊 Analyzing your calendar...")
    meetings_frame = load_calendar_dataframe(
        args.calendar,
        dataframe_path,
        force_import=args.force_import,
    )
    data_start, data_end = _frame_date_bounds(meetings_frame)
    meetings_frame = _filter_meetings_frame(meetings_frame, start_date, end_date)
    meetings_frame = _exclude_title_matches_frame(meetings_frame, excluded_title_patterns)
    summary = generate_summary(
        meetings_frame,
        options=SummaryOptions(
            num_titles=args.titles,
            num_times=args.times,
            period_start=start_date,
            period_end=end_date,
            data_start=data_start,
            data_end=data_end,
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


def _resolve_dataframe_path(dataframe_file: str | Path | None, calendar_file: str | Path | None) -> Path:
    """Return the saved Polars DataFrame path for this run."""
    if dataframe_file is not None:
        return Path(dataframe_file).expanduser().resolve()

    if calendar_file is not None:
        calendar_path = Path(calendar_file).expanduser()
        if calendar_path.suffix:
            return calendar_path.with_suffix(f"{calendar_path.suffix}.parquet").resolve()
        return calendar_path.with_suffix(".parquet").resolve()

    return (_user_cache_directory() / "meetings.parquet").resolve()


def _user_cache_directory() -> Path:
    """Return a platform-appropriate cache directory for saved meeting data."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "calendar-analyzer"
        return Path.home() / "AppData" / "Local" / "calendar-analyzer"

    return Path.home() / ".cache" / "calendar-analyzer"


def _cached_dataframe_is_usable(dataframe_path: Path, calendar_file: str | Path | None) -> bool:
    """Return whether an existing saved DataFrame can satisfy this run."""
    if not dataframe_path.exists():
        return False

    metadata = _read_cache_metadata(dataframe_path)
    if metadata.get("schema_version") != CACHE_SCHEMA_VERSION:
        return False

    if calendar_file is None:
        return True

    try:
        calendar_path = _resolve_calendar_source_path(Path(calendar_file))
    except OSError:
        return False

    return _metadata_matches_calendar(metadata, calendar_path)


def _metadata_matches_calendar(metadata: dict[str, object], calendar_path: Path) -> bool:
    """Return whether saved DataFrame metadata matches a requested calendar source."""
    try:
        source_path = _resolve_calendar_source_path(calendar_path)
    except OSError:
        return False

    if metadata.get("source_path") != str(source_path):
        return False

    try:
        source_stat = source_path.stat()
    except OSError:
        return False

    return (
        metadata.get("source_mtime_ns") == source_stat.st_mtime_ns
        and metadata.get("source_size") == source_stat.st_size
    )


def _read_cache_metadata(dataframe_path: Path) -> dict[str, object]:
    """Read saved DataFrame sidecar metadata, returning an empty mapping when absent."""
    metadata_path = _cache_metadata_path(dataframe_path)
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _cache_metadata_path(dataframe_path: Path) -> Path:
    """Return the metadata sidecar path for a saved DataFrame."""
    return dataframe_path.with_suffix(f"{dataframe_path.suffix}.metadata.json")


def _print_frame_coverage(label: str, frame: pl.DataFrame) -> None:
    """Print the date coverage for imported or loaded meeting data."""
    start_date, end_date = _frame_date_bounds(frame)
    if start_date is None or end_date is None:
        print(f"{label}: no timed meetings")
        return

    print(f"{label}: {_format_summary_date(start_date)} to {_format_summary_date(end_date)}")


def _frame_date_bounds(frame: pl.DataFrame) -> tuple[date | None, date | None]:
    """Return the min and max dates covered by a normalized meeting DataFrame."""
    if frame.is_empty():
        return None, None
    bounds = frame.select(pl.col("date").min().alias("start"), pl.col("date").max().alias("end")).row(0, named=True)
    return bounds["start"], bounds["end"]


def _format_summary_date(value: date | None) -> str:
    """Format an optional date for user-facing coverage output."""
    if value is None:
        return "No timed meetings"
    return value.strftime("%B %d, %Y")


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


def _resolve_calendar_source_path(calendar_path: Path) -> Path:
    """Return the concrete filesystem source used to import a calendar."""
    resolved_path = calendar_path.expanduser().resolve()
    if resolved_path.suffix.lower() != ".icbu":
        return resolved_path

    sqlite_db_path = resolved_path / "Calendar.sqlitedb"
    if sqlite_db_path.exists():
        return sqlite_db_path.resolve()

    ics_files = sorted(resolved_path.glob("*.ics"))
    if ics_files:
        return ics_files[0].resolve()

    return resolved_path


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


def _meetings_from_ics_events(calendar: Calendar) -> list[Meeting]:
    """Collect normalized meetings from VEVENT entries."""
    meetings: list[Meeting] = []

    for event in calendar.walk("VEVENT"):
        if _event_is_transparent(event) or _event_is_marked_all_day(event) or _event_is_non_meeting_status(event):
            continue

        raw_start = _event_start_datetime(event)
        if raw_start is None:
            continue

        start = convert_to_pacific(raw_start)
        duration_hours = _event_duration_hours(event, start)
        if (
            _is_all_day_like_calendar_block(start, duration_hours)
            or _is_floating_midnight(raw_start)
            or _is_default_duration_source_midnight(event, raw_start)
        ):
            continue

        meeting = _meeting_from_event(event, start, duration_hours)
        meetings.append(meeting)

    return meetings


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


def _event_is_non_meeting_status(event: Any) -> bool:
    """Return whether Microsoft ICS metadata marks an event as non-meeting time."""
    return any(
        _is_non_meeting_free_busy(event.get(property_name))
        for property_name in (
            "X-MICROSOFT-CDO-BUSYSTATUS",
            "X-MICROSOFT-CDO-INTENDEDSTATUS",
        )
    )


def _event_is_marked_all_day(event: Any) -> bool:
    """Return whether a VEVENT has vendor metadata marking it all-day."""
    return _event_has_truthy_property(
        event,
        (
            "X-MICROSOFT-CDO-ALLDAYEVENT",
            "X-MICROSOFT-MSNCALENDAR-ALLDAYEVENT",
        ),
    )


def _event_has_truthy_property(event: Any, property_names: tuple[str, ...]) -> bool:
    """Return whether any event property has a true-like value."""
    return any(_ics_truthy(event.get(property_name)) for property_name in property_names)


def _ics_truthy(value: object) -> bool:
    """Return whether an ICS property value is true-like."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _event_duration_hours(event: Any, start: datetime) -> float:
    """Resolve a VEVENT duration in hours from duration, dtend, or a default."""
    duration = event.get("duration")
    if isinstance(duration, timedelta):
        return _positive_duration_value_hours(duration.total_seconds() / 3600)

    duration_dt = getattr(duration, "dt", None)
    if isinstance(duration_dt, timedelta):
        return _positive_duration_value_hours(duration_dt.total_seconds() / 3600)

    if duration is not None:
        return _duration_string_hours(str(duration))

    return _dtend_duration_hours(event, start) or DEFAULT_DURATION_HOURS


def _duration_string_hours(duration: str) -> float:
    """Parse simple ICS duration strings like PT1H into hours."""
    if duration.startswith("PT") and duration.endswith("H"):
        try:
            return _positive_duration_value_hours(float(duration[2:-1]))
        except ValueError:
            return DEFAULT_DURATION_HOURS
    return DEFAULT_DURATION_HOURS


def _positive_duration_value_hours(duration_hours: float) -> float:
    """Return a positive explicit duration or the default meeting duration."""
    if duration_hours <= 0:
        return DEFAULT_DURATION_HOURS
    return duration_hours


def _dtend_duration_hours(event: Any, start: datetime) -> float | None:
    """Calculate event duration from dtend when it is available."""
    end = event.get("dtend")
    if end is None or not isinstance(end.dt, datetime):
        return None

    end_dt = convert_to_pacific(end.dt)
    return _positive_duration_hours(start, end_dt)


def _is_default_duration_source_midnight(event: Any, raw_start: datetime) -> bool:
    """Return whether an event looks like an all-day export downgraded to a default one-hour meeting."""
    return _starts_at_midnight(raw_start) and not _event_has_explicit_timed_duration(event)


def _event_has_explicit_timed_duration(event: Any) -> bool:
    """Return whether an ICS event has an explicit timed duration or end."""
    if event.get("duration") is not None:
        return True

    end = event.get("dtend")
    return end is not None and isinstance(end.dt, datetime)


def _meeting_from_event(event: Any, start: datetime, duration_hours: float) -> Meeting:
    """Normalize a VEVENT into the meeting shape used by reporting."""
    return {
        "start": start,
        "date": start.date(),
        "time": start.time(),
        "summary": str(event.get("summary", "No Title")),
        "duration_hours": duration_hours,
    }


def _fetch_sqlite_calendar_rows(
    calendar_path: Path,
    start_seconds: int | None = None,
    end_seconds: int | None = None,
) -> list[SqliteCalendarRow]:
    """Fetch SQLite calendar rows, optionally in an Apple-epoch date range."""
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
            """  # noqa: S608
        parameters: tuple[int, int] | tuple[()] = ()
        if start_seconds is not None and end_seconds is not None:
            query += " WHERE start_date >= ? AND start_date <= ?"
            parameters = (start_seconds, end_seconds)
        query += " ORDER BY start_date, summary"
        cursor.execute(query, parameters)
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


def _meetings_from_sqlite_rows(rows: Iterable[SqliteCalendarRow]) -> list[Meeting]:
    """Normalize SQLite calendar rows."""
    meetings: list[Meeting] = []

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
                "start": start_dt,
                "date": start_dt.date(),
                "time": start_dt.time(),
                "summary": summary or "No Title",
                "duration_hours": duration_hours,
            }
        )

    return meetings


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


def _meetings_from_olm_appointments(appointments: Iterable[Element]) -> list[Meeting]:
    """Normalize OLM appointment XML."""
    meetings: list[Meeting] = []
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
        end = _olm_appointment_end(appointment, start)
        duration_hours = _positive_duration_hours(start, end)
        if _is_all_day_like_calendar_block(start, duration_hours) or _is_olm_source_midnight(appointment):
            continue

        meetings.append(
            {
                "start": start,
                "date": start.date(),
                "time": start.time(),
                "summary": _xml_text(appointment, ("OPFCalendarEventCopySummary", "Subject", "Summary")) or "No Title",
                "duration_hours": duration_hours,
            }
        )

    if dated_appointments and dated_appointments == len(unparsable_start_values):
        examples = ", ".join(unparsable_start_values[:3])
        msg = f"Could not parse any OLM appointment start dates. Example value(s): {examples}"
        raise ValueError(msg)

    return meetings


def _olm_appointment_start(appointment: Element) -> datetime | None:
    """Return the start datetime from an OLM appointment."""
    start = _olm_datetime(_olm_appointment_start_text(appointment), require_time=True)
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
    """Return whether an OLM appointment is marked as non-meeting time."""
    return _is_non_meeting_free_busy(_xml_text(appointment, ("OPFCalendarEventCopyFreeBusyStatus", "FreeBusyStatus")))


def _olm_appointment_date_text(appointment: Element) -> str | None:
    """Return the raw OLM appointment date-only value."""
    return _xml_text(appointment, ("OPFCalendarEventCopyStartDate", "StartDate"))


def _olm_appointment_end(appointment: Element, start: datetime) -> datetime:
    """Return the end datetime from an OLM appointment."""
    end = _olm_datetime(_olm_appointment_end_text(appointment)) or _outlook_xml_split_datetime(
        appointment,
        ("OPFCalendarEventCopyEndDate", "EndDate"),
        ("OPFCalendarEventCopyEndTime", "EndTime"),
        fallback_date=start,
    )
    return end or start + timedelta(hours=DEFAULT_DURATION_HOURS)


def _olm_appointment_end_text(appointment: Element) -> str | None:
    """Return the raw OLM appointment end value."""
    return _xml_text(appointment, ("OPFCalendarEventCopyEndTime", "EndTime"))


def _is_olm_source_midnight(appointment: Element) -> bool:
    """Return whether an OLM appointment starts at source midnight."""
    return _olm_appointment_start_text_is_midnight(appointment)


def _olm_appointment_start_text_is_midnight(appointment: Element) -> bool:
    """Return whether the raw OLM start text has a midnight time component."""
    start_text = _olm_appointment_start_text(appointment)
    return start_text is not None and _datetime_text_starts_at_midnight(start_text)


def _datetime_text_starts_at_midnight(value: str) -> bool:
    """Return whether a date/time text contains a source midnight time."""
    text = value.strip().lower()
    return (
        text in {"00:00", "00:00:00", "12:00 am", "12:00:00 am"}
        or bool(re.search(r"(?:t|\s)00:00(?::00)?(?:z|[+-]\d{2}:?\d{2})?$", text))
        or bool(re.search(r"\s12:00(?::00)?\s*am$", text))
    )


def _olm_datetime(value: str | None, *, require_time: bool = False) -> datetime | None:
    """Parse an OLM date/time value, treating combined ISO values as UTC."""
    if value is None or not value.strip():
        return None

    text = value.strip()
    if "T" in text:
        parsed = _parse_olm_iso_datetime(text)
        if parsed is not None:
            return parsed
        if require_time and not _outlook_text_has_time(text):
            return None

    return _outlook_datetime(value, require_time=require_time)


def _parse_olm_iso_datetime(text: str) -> datetime | None:
    """Parse an OLM ISO datetime, assuming missing timezone data means UTC."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


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


def _meetings_from_outlook_csv_rows(rows: Iterable[dict[str, str]]) -> list[Meeting]:
    """Normalize Outlook CSV rows."""
    meetings: list[Meeting] = []

    for row in rows:
        start = _outlook_csv_start_datetime(row)
        if start is None:
            continue

        start = convert_to_pacific(start)
        end = _outlook_csv_end_datetime(row, start)
        duration_hours = _positive_duration_hours(start, end)
        if _is_all_day_like_calendar_block(start, duration_hours):
            continue

        meetings.append(
            {
                "start": start,
                "date": start.date(),
                "time": start.time(),
                "summary": _csv_value(row, ("Subject", "Title", "Summary")) or "No Title",
                "duration_hours": duration_hours,
            }
        )

    return meetings


def _outlook_csv_start_datetime(row: dict[str, str]) -> datetime | None:
    """Return the row start datetime, skipping all-day rows."""
    if _csv_truthy(_csv_value(row, ("All day event", "All Day Event", "All Day", "AllDayEvent"))):
        return None
    if _is_non_meeting_free_busy(_csv_value(row, ("Show Time As", "Show As", "Busy Status", "FreeBusyStatus"))):
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


def _is_non_meeting_free_busy(value: object) -> bool:
    """Return whether a free/busy value marks an item as non-meeting time."""
    if value is None:
        return False

    text = str(value).strip().lower()
    normalized_text = _normalize_csv_field(text)
    return text in {"0", "3"} or normalized_text in {"free", "transparent", "oof", "outofoffice"}


def _meetings_dataframe(meetings: list[Meeting] | pl.DataFrame) -> pl.DataFrame:
    """Return normalized meetings as a Polars DataFrame with the reporting schema."""
    if isinstance(meetings, pl.DataFrame):
        return meetings.select(list(MEETING_FRAME_SCHEMA)).cast(MEETING_FRAME_SCHEMA)

    rows = [
        {
            "start": _meeting_local_start(meeting),
            "date": meeting["date"],
            "time": meeting["time"],
            "summary": meeting["summary"],
            "duration_hours": float(meeting["duration_hours"]),
        }
        for meeting in meetings
    ]
    if not rows:
        return pl.DataFrame(schema=MEETING_FRAME_SCHEMA)
    return pl.DataFrame(rows, schema=MEETING_FRAME_SCHEMA, orient="row")


def _meeting_local_start(meeting: Meeting) -> datetime:
    """Return a Pacific local naive datetime for cache filtering."""
    return _local_naive_datetime(meeting["start"])


def _local_naive_datetime(value: datetime) -> datetime:
    """Return a timezone-free Pacific local datetime for Polars comparisons."""
    return convert_to_pacific(value).replace(tzinfo=None)


def _filter_meetings_frame(frame: pl.DataFrame, start_date: datetime, end_date: datetime) -> pl.DataFrame:
    """Filter normalized meetings to an inclusive datetime window."""
    if frame.is_empty():
        return frame

    return frame.filter(
        (pl.col("start") >= _local_naive_datetime(start_date)) & (pl.col("start") <= _local_naive_datetime(end_date))
    )


def _read_meetings_dataframe(dataframe_path: Path) -> pl.DataFrame:
    """Read normalized meetings from a saved Polars/Parquet DataFrame."""
    try:
        frame = pl.read_parquet(dataframe_path)
    except (OSError, pl.exceptions.PolarsError) as error:
        raise SavedDataFrameReadError(f"Error reading saved Polars DataFrame: {error}") from error

    missing_columns = set(MEETING_FRAME_SCHEMA) - set(frame.columns)
    if missing_columns:
        raise SavedDataFrameReadError(
            f"Error reading saved Polars DataFrame: missing columns {sorted(missing_columns)}"
        )

    return _meetings_dataframe(frame)


def _write_meetings_dataframe(frame: pl.DataFrame, dataframe_path: Path, calendar_path: Path) -> None:
    """Write normalized meetings and source metadata to disk."""
    parquet_temp_path: Path | None = None
    metadata_temp_path: Path | None = None
    try:
        dataframe_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = _cache_metadata_path(dataframe_path)
        parquet_temp_path = _temporary_sibling_path(dataframe_path)
        metadata_temp_path = _temporary_sibling_path(metadata_path)

        frame.write_parquet(parquet_temp_path)
        metadata_temp_path.write_text(_cache_metadata(calendar_path, frame), encoding="utf-8")
        parquet_temp_path.replace(dataframe_path)
        parquet_temp_path = None
        metadata_temp_path.replace(metadata_path)
        metadata_temp_path = None
    except (OSError, pl.exceptions.PolarsError) as error:
        print(f"Error saving Polars DataFrame: {error}")
        raise_system_exit()
    finally:
        for temp_path in (parquet_temp_path, metadata_temp_path):
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()


def _temporary_sibling_path(destination: Path) -> Path:
    """Return an empty temporary path in the same directory as the destination."""
    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(file_descriptor)
    return Path(temp_name)


def _cache_metadata(calendar_path: Path, frame: pl.DataFrame) -> str:
    """Return JSON sidecar metadata for a saved DataFrame."""
    try:
        source_path = _resolve_calendar_source_path(calendar_path)
        source_stat = source_path.stat()
    except OSError:
        source_path = calendar_path
        source_mtime_ns = None
        source_size = None
    else:
        source_mtime_ns = source_stat.st_mtime_ns
        source_size = source_stat.st_size

    data_start, data_end = _frame_date_bounds(frame)
    metadata = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "source_path": str(source_path),
        "source_mtime_ns": source_mtime_ns,
        "source_size": source_size,
        "data_start": data_start.isoformat() if data_start is not None else None,
        "data_end": data_end.isoformat() if data_end is not None else None,
    }
    return json.dumps(metadata, indent=2, sort_keys=True)


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


def _exclude_title_matches_frame(
    frame: pl.DataFrame,
    excluded_title_patterns: list[re.Pattern[str]],
) -> pl.DataFrame:
    """Remove meetings whose titles match any excluded title regex."""
    if not excluded_title_patterns or frame.is_empty():
        return frame

    rows = [
        row
        for row in frame.iter_rows(named=True)
        if not any(pattern.search(row["summary"]) for pattern in excluded_title_patterns)
    ]
    return (
        pl.DataFrame(rows, schema=MEETING_FRAME_SCHEMA, orient="row")
        if rows
        else pl.DataFrame(schema=MEETING_FRAME_SCHEMA)
    )


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the calendar analyzer CLI."""
    parser = argparse.ArgumentParser(description="Analyze calendar events from a specified date range.")
    parser.add_argument("--calendar", help="Path to the exported calendar file (.ics, .olm, .csv, .icbu, .sqlitedb)")
    parser.add_argument(
        "--dataframe",
        help="Path to the saved Polars/Parquet meeting data (default: derived from --calendar or user cache)",
    )
    parser.add_argument(
        "--import",
        action="store_true",
        dest="force_import",
        help="Import the calendar export and refresh the saved Polars DataFrame even when cached data exists",
    )
    parser.add_argument("--start-date", help="Start date for analysis (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date for analysis (YYYY-MM-DD)")
    parser.add_argument(
        "--days",
        type=_positive_int_argument,
        default=DEFAULT_DAYS_BACK,
        help=f"Number of days to look back from end date (default: {DEFAULT_DAYS_BACK})",
    )
    parser.add_argument(
        "--times",
        type=_positive_int_argument,
        default=5,
        help="Number of meeting times to display (default: 5)",
    )
    parser.add_argument(
        "--titles",
        type=_positive_int_argument,
        default=50,
        help="Number of meeting titles to display (default: 50)",
    )
    parser.add_argument(
        "--exclude-title",
        action="append",
        dest="exclude_titles",
        help="Case-insensitive regex for meeting titles to exclude from statistics; may be repeated",
    )
    parser.add_argument("--output", help="Path to save the analysis summary (default: print to console)")
    return parser.parse_args()


def _positive_int_argument(value: str) -> int:
    """Parse an argparse integer that must be greater than zero."""
    try:
        parsed_value = int(value)
    except ValueError as error:
        msg = "must be a positive integer"
        raise argparse.ArgumentTypeError(msg) from error
    if parsed_value <= 0:
        msg = "must be a positive integer"
        raise argparse.ArgumentTypeError(msg)
    return parsed_value


def _parse_date_argument(value: str | None, label: str, *, end_of_day: bool = False) -> datetime | None:
    """Parse an optional YYYY-MM-DD CLI date into a Pacific-aware datetime."""
    if value is None:
        return None

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        print(f"Error: {label} date must be in YYYY-MM-DD format")
        raise_system_exit()

    try:
        parsed_date = date.fromisoformat(value)
    except ValueError:
        print(f"Error: {label} date must be in YYYY-MM-DD format")
        raise_system_exit()
    parsed_time = time.max if end_of_day else time.min
    return datetime.combine(parsed_date, parsed_time, tzinfo=PACIFIC)


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
    summary_text = f"\n{summary}\n"
    stdout_buffer = getattr(sys.stdout, "buffer", None)
    if stdout_buffer is not None:
        sys.stdout.flush()
        stdout_buffer.write(summary_text.encode())
        stdout_buffer.flush()
        return

    sys.stdout.writelines([summary_text])
    sys.stdout.flush()


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
