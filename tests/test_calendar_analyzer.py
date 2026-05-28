"""Tests for calendar_analyzer module."""

import importlib
import io
import json
import os
import sqlite3
import tempfile
import textwrap
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch

import polars as pl
import pytest

import calendar_analyzer


class BufferedTextStdout:
    """Text stream test double with a binary buffer below it."""

    def __init__(self) -> None:
        """Create empty pending and flushed output buffers."""
        self.buffer = BinaryStdoutBuffer(self)
        self._pending: list[str] = []
        self._flushed: list[str] = []

    def write(self, text: str) -> int:
        """Buffer text writes until flush is called."""
        self._pending.append(text)
        return len(text)

    def flush(self) -> None:
        """Move pending text writes into observable output."""
        self._flushed.extend(self._pending)
        self._pending.clear()

    def write_bytes(self, content: bytes) -> int:
        """Write decoded bytes directly to observable output."""
        self._flushed.append(content.decode())
        return len(content)

    def getvalue(self) -> str:
        """Return flushed and pending text output."""
        return "".join([*self._flushed, *self._pending])


class BinaryStdoutBuffer:
    """Binary stream test double attached to BufferedTextStdout."""

    def __init__(self, stdout: BufferedTextStdout) -> None:
        """Store the parent text stream."""
        self._stdout = stdout

    def write(self, content: bytes) -> int:
        """Write decoded bytes directly to observable output."""
        return self._stdout.write_bytes(content)

    def flush(self) -> None:
        """Flush the binary stream."""


def create_temp_sqlite_calendar(
    meetings: list[tuple[str | None, datetime, datetime, int]] | None = None,
) -> str:
    """Helper function to create a temporary Apple Calendar SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".sqlitedb", delete=False) as tmp:
        tmp_path = tmp.name
    with closing(sqlite3.connect(tmp_path)) as conn:
        conn.execute("CREATE TABLE CalendarItem (summary TEXT, start_date INTEGER, end_date INTEGER, all_day INTEGER)")
        conn.executemany(
            "INSERT INTO CalendarItem (summary, start_date, end_date, all_day) VALUES (?, ?, ?, ?)",
            [
                (
                    summary,
                    int((start - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                    int((end - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                    all_day,
                )
                for summary, start, end, all_day in meetings or []
            ],
        )
        conn.commit()
    return tmp_path


def create_temp_olm_file(calendar_xml: str) -> str:
    """Helper function to create a temporary OLM-like archive with Calendar.xml."""
    with tempfile.NamedTemporaryFile(suffix=".olm", delete=False) as tmp:
        tmp_path = tmp.name
    with zipfile.ZipFile(tmp_path, "w") as archive:
        archive.writestr("Accounts/Calendar.xml", calendar_xml)
    return tmp_path


def create_temp_dummy_file(suffix: str = ".sqlitedb") -> str:
    """Helper function to create a temporary dummy file path.

    Args:
        suffix (str): File suffix (default: ".sqlitedb")

    Returns:
        str: Path to the created temporary file

    Note:
        The caller is responsible for cleaning up the file using os.unlink()
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as dummy_file:
        return dummy_file.name


def meeting_frame(meetings: list[dict[str, Any]]) -> pl.DataFrame:
    """Return normalized meeting dictionaries as a reporting DataFrame."""
    rows = [
        {
            "start": meeting.get("start", datetime.combine(meeting["date"], meeting["time"])),
            "date": meeting["date"],
            "time": meeting["time"],
            "summary": meeting["summary"],
            "duration_hours": meeting["duration_hours"],
        }
        for meeting in meetings
    ]
    return pl.DataFrame(rows, schema=calendar_analyzer.MEETING_FRAME_SCHEMA, orient="row")


def analysis_result(frame: pl.DataFrame) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    """Return row dictionaries and aggregate stats for behavior assertions."""
    meetings = list(frame.iter_rows(named=True))
    stats = {
        "total_meetings": frame.height,
        "total_hours": 0.0 if frame.is_empty() else frame.select(pl.col("duration_hours").sum()).item(),
    }
    return meetings, stats


def test_analyze_mock_sqlite(monkeypatch, capsys) -> None:
    """Test analyzing a mock Apple Calendar SQLite database."""
    tmp_path = create_temp_sqlite_calendar(
        [
            (
                "Test Meeting",
                datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                0,
            ),
            (
                "Project Sync",
                datetime(2023, 7, 2, 22, 0, tzinfo=UTC),
                datetime(2023, 7, 3, 0, 0, tzinfo=UTC),
                0,
            ),
        ]
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            tmp_path,
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
            "--titles",
            "10",
        ],
    )

    calendar_analyzer.main()

    out = capsys.readouterr().out
    assert "Test Meeting" in out
    assert "Project Sync" in out
    assert "Total Meetings: 2" in out
    assert "Total Meeting Hours: 3.0" in out


def test_main_excludes_titles_by_regex(monkeypatch, capsys) -> None:
    """Test title exclusion regexes remove matching meetings from stats and output."""
    calendar_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "SVM Town Hall",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                ),
                (
                    "All VMTH Meeting",
                    datetime(2023, 7, 1, 19, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 20, 0, tzinfo=UTC),
                    0,
                ),
                (
                    "Project Sync",
                    datetime(2023, 7, 1, 21, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 22, 0, tzinfo=UTC),
                    0,
                ),
            ]
        )
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            str(calendar_path),
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
            "--exclude-title",
            "svm",
            "--exclude-title",
            "VMTH|State of the Hospital",
        ],
    )

    calendar_analyzer.main()

    out = capsys.readouterr().out
    assert "Project Sync" in out
    assert "SVM Town Hall" not in out
    assert "All VMTH Meeting" not in out
    assert "Total Meetings: 1" in out
    assert "Total Meeting Hours: 1.0" in out


def test_main_invalid_exclude_title_regex_exits(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test invalid title exclusion regexes fail before analysis."""
    calendar_path = tmp_path / "calendar.sqlitedb"
    calendar_path.write_bytes(b"")
    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            str(calendar_path),
            "--exclude-title",
            "[",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    assert "Error: Invalid --exclude-title regex '[':" in capsys.readouterr().out


def test_invalid_start_date_format(monkeypatch, capsys) -> None:
    """Test that invalid start date format causes system exit."""
    # Create a temporary dummy file path (secure alternative to mktemp)
    dummy_path = create_temp_dummy_file()

    monkeypatch.setattr("sys.argv", ["calendar-analyzer", "--calendar", dummy_path, "--start-date", "invalid-date"])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error: Start date must be in YYYY-MM-DD format" in out


def test_invalid_end_date_format(monkeypatch, capsys) -> None:
    """Test that invalid end date format causes system exit."""
    # Create a temporary dummy file path (secure alternative to mktemp)
    dummy_path = create_temp_dummy_file()

    monkeypatch.setattr("sys.argv", ["calendar-analyzer", "--calendar", dummy_path, "--end-date", "2023/01/01"])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error: End date must be in YYYY-MM-DD format" in out


@pytest.mark.parametrize("start_date", ["20230701", "2023-W26-6"])
def test_start_date_rejects_non_extended_iso_formats(monkeypatch, capsys, start_date: str) -> None:
    """Test start dates must use the documented YYYY-MM-DD spelling."""
    dummy_path = create_temp_dummy_file()

    monkeypatch.setattr("sys.argv", ["calendar-analyzer", "--calendar", dummy_path, "--start-date", start_date])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    assert "Error: Start date must be in YYYY-MM-DD format" in capsys.readouterr().out
    Path(dummy_path).unlink()


@pytest.mark.parametrize(
    ("option", "value"), [("--days", "0"), ("--times", "-1"), ("--titles", "0"), ("--days", "abc")]
)
def test_positive_integer_arguments_reject_non_positive_values(monkeypatch, capsys, option: str, value: str) -> None:
    """Test count and range arguments must be positive integers."""
    monkeypatch.setattr("sys.argv", ["calendar-analyzer", option, value])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 2
    assert f"argument {option}: must be a positive integer" in capsys.readouterr().err


def test_end_date_before_start_date(monkeypatch, capsys) -> None:
    """Test that end date before start date causes system exit."""
    # Create a temporary dummy file path (secure alternative to mktemp)
    dummy_path = create_temp_dummy_file()

    monkeypatch.setattr(
        "sys.argv",
        ["calendar-analyzer", "--calendar", dummy_path, "--start-date", "2023-07-01", "--end-date", "2023-06-30"],
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error: End date cannot be before start date" in out
    assert "Start date: 2023-07-01" in out
    assert "End date: 2023-06-30" in out


def test_valid_date_formats(monkeypatch, capsys) -> None:
    """Test that valid date formats are parsed correctly."""
    tmp_path = create_temp_sqlite_calendar(
        [
            (
                "Test Meeting",
                datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                0,
            )
        ]
    )

    monkeypatch.setattr(
        "sys.argv",
        ["calendar-analyzer", "--calendar", tmp_path, "--start-date", "2023-06-30", "--end-date", "2023-07-31"],
    )

    calendar_analyzer.main()

    out = capsys.readouterr().out
    assert "Test Meeting" in out


def test_cli_end_date_includes_entire_calendar_day(monkeypatch, capsys) -> None:
    """Test --end-date includes meetings later on that date."""
    calendar_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "End Date Noon Meeting",
                    datetime(2024, 7, 31, 19, 0, tzinfo=UTC),
                    datetime(2024, 7, 31, 20, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            str(calendar_path),
            "--start-date",
            "2024-07-01",
            "--end-date",
            "2024-07-31",
        ],
    )

    calendar_analyzer.main()

    assert "End Date Noon Meeting" in capsys.readouterr().out


def test_edge_case_dates(monkeypatch, capsys) -> None:
    """Test edge case date formats."""
    # Test leap year date
    # Create a temporary dummy file path that doesn't exist
    dummy_path = create_temp_dummy_file()
    # Remove the file to make it nonexistent (for this test)
    Path(dummy_path).unlink()

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            dummy_path,
            "--start-date",
            "2024-02-29",  # Valid leap year date
        ],
    )

    with pytest.raises(SystemExit):  # Will fail because dummy file doesn't exist
        calendar_analyzer.main()

    # Test invalid leap year date
    dummy_path2 = create_temp_dummy_file()
    # Remove this file too since we want to test date validation, not file reading
    Path(dummy_path2).unlink()

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            dummy_path2,
            "--start-date",
            "2023-02-29",  # Invalid - 2023 is not a leap year
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error: Start date must be in YYYY-MM-DD format" in out


def test_convert_to_pacific() -> None:
    """Test timezone conversion function."""
    # Test UTC to Pacific conversion
    utc_time = datetime(2023, 7, 1, 17, 0, 0, tzinfo=UTC)
    pacific_time = calendar_analyzer.convert_to_pacific(utc_time)

    # During PDT (July), UTC-7
    assert pacific_time.hour == 10  # 17:00 UTC = 10:00 PDT

    # Test naive datetime (assumed UTC)
    naive_time = datetime.fromisoformat("2023-07-01T17:00:00")
    pacific_time = calendar_analyzer.convert_to_pacific(naive_time)
    assert pacific_time.hour == 10


def test_print_calendar_export_instructions(capsys) -> None:
    """Test calendar export instructions function."""
    calendar_analyzer.print_calendar_export_instructions()

    out = capsys.readouterr().out
    assert "Please export your calendar from Calendar or Outlook:" in out
    assert "Apple Calendar on macOS: use File > Export > Calendar Archive" in out
    assert "Microsoft Outlook for Mac: export a legacy Outlook archive (.olm)" in out
    assert "Classic Microsoft Outlook for Windows: export an Outlook Data File (.pst)" in out
    assert "New Outlook for Windows has limited PST calendar support" in out
    assert "calendar-analyzer --calendar" in out


def test_generate_summary_no_meetings() -> None:
    """Test generate_summary with no meetings."""
    result = calendar_analyzer.generate_summary(meeting_frame([]))
    assert result == "No meetings found in the specified time period."


def test_generate_summary_no_meetings_shows_optional_ranges() -> None:
    """Test empty summaries still show requested coverage and query bounds."""
    result = calendar_analyzer.generate_summary(
        meeting_frame([]),
        calendar_analyzer.SummaryOptions(
            data_end=datetime(2023, 12, 31, tzinfo=calendar_analyzer.PACIFIC).date(),
            period_end=datetime(2023, 7, 31, tzinfo=calendar_analyzer.PACIFIC),
        ),
    )

    assert "No meetings found in the specified time period." in result
    assert "Imported Data Coverage:" in result
    assert "- From: No timed meetings" in result
    assert "- To:   December 31, 2023" in result
    assert "Query Date Range:" in result
    assert "- To:   July 31, 2023" in result


def test_file_output_functionality(monkeypatch, capsys) -> None:
    """Test saving analysis to a file."""
    tmp_calendar_path = create_temp_sqlite_calendar(
        [
            (
                "Test Meeting",
                datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                0,
            )
        ]
    )

    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tmp_output:
        output_path = tmp_output.name

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            tmp_calendar_path,
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
            "--output",
            output_path,
        ],
    )

    calendar_analyzer.main()

    # Check that file was created and contains expected content
    with Path(output_path).open(encoding="utf-8") as f:
        content = f.read()
        assert "Test Meeting" in content
        assert "Calendar Analysis Summary" in content

    out = capsys.readouterr().out
    assert f"Analysis saved to: {output_path}" in out

    # Clean up
    Path(tmp_calendar_path).unlink()
    Path(output_path).unlink()


def test_generate_prompt_uses_saved_dataframe_without_calendar(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test generating a paste-ready AI prompt from cached meeting data."""
    calendar_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Prompt Format Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    )
    cache_dir = tmp_path / "cache"
    output_path = tmp_path / "calendar-prompt.txt"
    monkeypatch.setattr(calendar_analyzer, "_user_cache_directory", lambda: cache_dir)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            str(calendar_path),
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )
    calendar_analyzer.main()
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
            "--generate-prompt",
        ],
    )
    calendar_analyzer.main()

    captured = capsys.readouterr()
    content = output_path.read_text(encoding="utf-8")
    assert content.startswith("Please analyze this calendar summary so I can understand how my meeting time was spent.")
    assert "draft concise bullets I could adapt for weekly updates or year-end accomplishment summaries" in content
    assert "Privacy note: this summary was generated locally." in content
    assert "Calendar summary:\n```text" in content
    assert "Calendar Analysis Summary" in content
    assert "Query Date Range:" in content
    assert "Imported Data Coverage:" not in content
    assert "Prompt Format Meeting" in content
    assert content.endswith("```")
    assert f"Found saved Polars DataFrame at: {cache_dir / 'meetings.parquet'}" in captured.out
    assert f"Prompt saved to: {output_path.name}" in captured.out
    assert "No calendar files found" not in captured.out


def test_generate_prompt_errors_when_requested_range_is_outside_cache(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test prompt generation explains when cached data does not cover the requested range."""
    calendar_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Cached Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    )
    cache_dir = tmp_path / "cache"
    output_path = tmp_path / "calendar-prompt.txt"
    monkeypatch.setattr(calendar_analyzer, "_user_cache_directory", lambda: cache_dir)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            str(calendar_path),
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )
    calendar_analyzer.main()
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--start-date",
            "2025-05-01",
            "--end-date",
            "2026-04-30",
            "--generate-prompt",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    assert not output_path.exists()
    out = capsys.readouterr().out
    assert "Error: no cached meeting data overlaps the requested prompt date range." in out
    assert "Cached meeting data covers: July 01, 2023 to July 01, 2023" in out
    assert "Requested prompt range: May 01, 2025 to April 30, 2026" in out
    assert "Refresh the cache with: calendar-analyzer --import" in out


def test_file_output_error(monkeypatch, capsys) -> None:
    """Test file output error handling."""
    tmp_path = create_temp_sqlite_calendar(
        [
            (
                "Test Meeting",
                datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                0,
            )
        ]
    )

    with tempfile.TemporaryDirectory() as output_dir:
        monkeypatch.setattr(
            "sys.argv",
            [
                "calendar-analyzer",
                "--calendar",
                tmp_path,
                "--start-date",
                "2023-06-30",
                "--end-date",
                "2023-07-03",
                "--output",
                output_dir,
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error saving to file:" in out

    # Clean up
    Path(tmp_path).unlink()


def test_file_output_error_preserves_existing_file(monkeypatch, capsys) -> None:
    """Test failed output replacement preserves existing file content."""
    tmp_calendar_path = create_temp_sqlite_calendar(
        [
            (
                "Test Meeting",
                datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                0,
            )
        ]
    )
    output_path = Path(create_temp_dummy_file(".txt"))
    output_path.write_text("existing content", encoding="utf-8")

    def raise_during_write(*_args, **_kwargs) -> NoReturn:
        message = "simulated write failure"
        raise OSError(message)

    monkeypatch.setattr("tempfile.NamedTemporaryFile", raise_during_write)
    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            tmp_calendar_path,
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
            "--output",
            str(output_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    assert output_path.read_text(encoding="utf-8") == "existing content"
    assert "Error saving to file: simulated write failure" in capsys.readouterr().out
    Path(tmp_calendar_path).unlink()
    output_path.unlink()


def test_main_uses_saved_dataframe_without_calendar(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test the CLI can report from a saved Polars DataFrame before finding a calendar."""
    source_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Cached Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    )
    dataframe_path = tmp_path / "meetings.parquet"
    calendar_analyzer.load_calendar_dataframe(source_path, dataframe_path, force_import=True)
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--dataframe",
            str(dataframe_path),
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )

    calendar_analyzer.main()

    out = capsys.readouterr().out
    assert f"Found saved Polars DataFrame at: {dataframe_path}" in out
    assert "Cached Meeting" in out
    assert "No calendar files found" not in out


def test_main_import_option_refreshes_saved_dataframe(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test --import refreshes cached meeting data from the calendar export."""
    calendar_path = tmp_path / "calendar.sqlitedb"
    dataframe_path = tmp_path / "meetings.parquet"
    Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Stale Cached Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    ).replace(calendar_path)
    calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path, force_import=True)
    capsys.readouterr()
    calendar_path.unlink()
    Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Imported Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    ).replace(calendar_path)

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            str(calendar_path),
            "--dataframe",
            str(dataframe_path),
            "--import",
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )

    calendar_analyzer.main()

    out = capsys.readouterr().out
    assert "Forcing calendar import because --import was supplied." in out
    assert "Imported Meeting" in out
    assert "Stale Cached Meeting" not in out


def test_load_calendar_dataframe_tracks_icbu_sqlite_metadata_changes(capsys, tmp_path: Path) -> None:
    """Test ICBU cache validity follows the embedded SQLite database."""
    icbu_path = tmp_path / "backup.icbu"
    icbu_path.mkdir()
    sqlite_path = icbu_path / "Calendar.sqlitedb"
    dataframe_path = tmp_path / "meetings.parquet"
    Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Stale Inner Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    ).replace(sqlite_path)
    calendar_analyzer.load_calendar_dataframe(icbu_path, dataframe_path, force_import=True)
    metadata_path = dataframe_path.with_suffix(f"{dataframe_path.suffix}.metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["source_path"] == str(sqlite_path.resolve())
    capsys.readouterr()

    sqlite_path.unlink()
    Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Imported Inner Meeting With New Size",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    ).replace(sqlite_path)

    frame = calendar_analyzer.load_calendar_dataframe(icbu_path, dataframe_path)

    out = capsys.readouterr().out
    assert f"Saved Polars DataFrame is stale or from another calendar: {dataframe_path}" in out
    assert [row["summary"] for row in frame.iter_rows(named=True)] == ["Imported Inner Meeting With New Size"]


def test_load_calendar_dataframe_tracks_icbu_sqlite_metadata(capsys, tmp_path: Path) -> None:
    """Test ICBU cache metadata prefers the embedded SQLite database."""
    icbu_path = tmp_path / "backup.icbu"
    icbu_path.mkdir()
    sqlite_path = icbu_path / "Calendar.sqlitedb"
    with closing(sqlite3.connect(sqlite_path)) as conn:
        conn.execute("CREATE TABLE CalendarItem (summary TEXT, start_date INTEGER, end_date INTEGER)")
        conn.commit()

    dataframe_path = tmp_path / "meetings.parquet"
    frame = calendar_analyzer.load_calendar_dataframe(icbu_path, dataframe_path, force_import=True)
    metadata_path = dataframe_path.with_suffix(f"{dataframe_path.suffix}.metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert frame.is_empty()
    assert metadata["source_path"] == str(sqlite_path.resolve())
    assert f"Found SQLite database in ICBU backup: {sqlite_path}" in capsys.readouterr().out


def test_main_reimports_when_saved_dataframe_is_corrupt(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test a corrupt saved DataFrame is rebuilt when the calendar source is available."""
    calendar_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Recovered Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    )
    dataframe_path = tmp_path / "meetings.parquet"
    calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path, force_import=True)
    dataframe_path.write_text("not parquet", encoding="utf-8")
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            str(calendar_path),
            "--dataframe",
            str(dataframe_path),
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )

    calendar_analyzer.main()

    out = capsys.readouterr().out
    assert "Error reading saved Polars DataFrame:" in out
    assert "Saved Polars DataFrame could not be read; importing calendar export instead." in out
    assert "Recovered Meeting" in out


def test_main_exits_for_corrupt_saved_dataframe_without_calendar(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test cache-only runs fail clearly when the saved DataFrame cannot be read."""
    calendar_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Cached Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    )
    dataframe_path = tmp_path / "meetings.parquet"
    calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path, force_import=True)
    dataframe_path.write_text("not parquet", encoding="utf-8")
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--dataframe",
            str(dataframe_path),
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error reading saved Polars DataFrame:" in out
    assert "Saved Polars DataFrame could not be read; importing calendar export instead." not in out
    assert "No calendar files found" not in out


def test_main_exits_for_saved_dataframe_missing_columns(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test cache-only runs report saved DataFrames with missing schema columns."""
    calendar_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Cached Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    )
    dataframe_path = tmp_path / "meetings.parquet"
    calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path, force_import=True)
    pl.read_parquet(dataframe_path).drop("duration_hours").write_parquet(dataframe_path)
    capsys.readouterr()

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--dataframe",
            str(dataframe_path),
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error reading saved Polars DataFrame: missing columns ['duration_hours']" in out
    assert "No calendar files found" not in out


def test_write_meetings_dataframe_preserves_existing_cache_on_failure(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test failed cache rewrites keep the previous readable DataFrame."""
    calendar_path = tmp_path / "calendar.sqlitedb"
    dataframe_path = tmp_path / "meetings.parquet"
    Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Original Cache",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    ).replace(calendar_path)
    calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path, force_import=True)
    calendar_path.unlink()
    Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Replacement Cache",
                    datetime(2023, 7, 2, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 2, 19, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    ).replace(calendar_path)

    def fail_metadata(*_args, **_kwargs) -> NoReturn:
        message = "simulated metadata failure"
        raise OSError(message)

    monkeypatch.setattr(calendar_analyzer, "_cache_metadata", fail_metadata)

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path, force_import=True)

    assert exc_info.value.code == 1
    assert "Error saving Polars DataFrame: simulated metadata failure" in capsys.readouterr().out
    rows = list(pl.read_parquet(dataframe_path).iter_rows(named=True))
    assert [row["summary"] for row in rows] == ["Original Cache"]


def test_load_calendar_dataframe_rebuilds_bad_or_stale_metadata(capsys, tmp_path: Path) -> None:
    """Test cached DataFrames are rebuilt when sidecar metadata is unusable."""
    dataframe_path = tmp_path / "meetings.parquet"
    calendar_path = tmp_path / "calendar.sqlitedb"
    metadata_path = dataframe_path.with_suffix(f"{dataframe_path.suffix}.metadata.json")
    meeting_frame([]).write_parquet(dataframe_path)
    Path(create_temp_sqlite_calendar()).replace(calendar_path)

    metadata_path.write_text("{not json", encoding="utf-8")
    assert calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path).is_empty()
    assert f"Saved Polars DataFrame is stale or from another calendar: {dataframe_path}" in capsys.readouterr().out

    metadata_path.write_text(json.dumps({"schema_version": 0}), encoding="utf-8")
    assert calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path).is_empty()
    assert f"Saved Polars DataFrame is stale or from another calendar: {dataframe_path}" in capsys.readouterr().out

    source_stat = calendar_path.stat()
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": calendar_analyzer.CACHE_SCHEMA_VERSION,
                "source_path": str(calendar_path.resolve()),
                "source_mtime_ns": source_stat.st_mtime_ns,
                "source_size": source_stat.st_size + 1,
            }
        ),
        encoding="utf-8",
    )
    assert calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path).is_empty()
    assert f"Saved Polars DataFrame is stale or from another calendar: {dataframe_path}" in capsys.readouterr().out


def test_cache_metadata_handles_unstatable_calendar_source(monkeypatch, tmp_path: Path) -> None:
    """Test cache metadata records unknown source stats when stat fails."""
    calendar_path = Path(create_temp_sqlite_calendar())
    dataframe_path = tmp_path / "meetings.parquet"
    metadata_path = dataframe_path.with_suffix(f"{dataframe_path.suffix}.metadata.json")

    def raise_source_error(_calendar_path: Path) -> Path:
        message = "source vanished"
        raise OSError(message)

    monkeypatch.setattr("calendar_analyzer._resolve_calendar_source_path", raise_source_error)

    calendar_analyzer.load_calendar_dataframe(calendar_path, dataframe_path, force_import=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["source_path"] == str(calendar_path.resolve())
    assert metadata["source_mtime_ns"] is None
    assert metadata["source_size"] is None


def test_main_supports_text_only_stdout(monkeypatch) -> None:
    """Test summary output works when stdout has no binary buffer."""
    tmp_calendar_path = create_temp_sqlite_calendar(
        [
            (
                "Test Meeting",
                datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                0,
            )
        ]
    )
    stdout = io.StringIO()
    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            tmp_calendar_path,
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )
    monkeypatch.setattr(calendar_analyzer.sys, "stdout", stdout)

    calendar_analyzer.main()

    out = stdout.getvalue()
    assert "Calendar Analysis Summary" in out
    assert "Test Meeting" in out
    Path(tmp_calendar_path).unlink()


def test_main_flushes_status_output_before_binary_summary(monkeypatch) -> None:
    """Test binary summary writes preserve earlier text output order."""
    calendar_path = Path(
        create_temp_sqlite_calendar(
            [
                (
                    "Ordered Output Meeting",
                    datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                    datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                    0,
                )
            ]
        )
    )
    stdout = BufferedTextStdout()
    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            str(calendar_path),
            "--start-date",
            "2023-06-30",
            "--end-date",
            "2023-07-03",
        ],
    )
    monkeypatch.setattr(calendar_analyzer.sys, "stdout", stdout)

    calendar_analyzer.main()

    out = stdout.getvalue()
    assert out.index("📊 Analyzing your calendar...") < out.index("Calendar Analysis Summary")
    assert "Ordered Output Meeting" in out


def test_calendar_file_read_error(monkeypatch, capsys) -> None:
    """Test error handling when calendar file cannot be read."""
    monkeypatch.setattr("sys.argv", ["calendar-analyzer", "--calendar", "/nonexistent/file.pst"])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error reading Outlook PST calendar: file does not exist:" in out


def test_generate_summary_with_long_titles() -> None:
    """Test generate_summary with very long meeting titles."""
    # Create meetings with long titles
    long_title = "A" * 150  # 150 character title
    meetings = meeting_frame(
        [
            {
                "date": datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC).date(),
                "time": datetime(2023, 7, 1, 10, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
                "summary": long_title,
                "duration_hours": 1.0,
            },
            {
                "date": datetime(2023, 7, 3, tzinfo=calendar_analyzer.PACIFIC).date(),
                "time": datetime(2023, 7, 1, 14, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
                "summary": "Short title",
                "duration_hours": 1.0,
            },
        ]
    )

    result = calendar_analyzer.generate_summary(meetings, calendar_analyzer.SummaryOptions(num_titles=5))

    # Long title should be truncated
    assert "A" * 100 + "..." in result
    assert "Short title" in result
    assert "Total Meetings: 2" in result
    assert "Average Meetings per Day: 0.7" in result


def test_generate_summary_limits_common_times_and_titles() -> None:
    """Test summary uses requested limits for common times and titles."""
    meetings = meeting_frame(
        [
            {
                "date": datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC).date(),
                "time": datetime(2023, 7, 1, 9, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
                "summary": "Frequent",
                "duration_hours": 1.0,
            },
            {
                "date": datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC).date(),
                "time": datetime(2023, 7, 1, 9, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
                "summary": "Frequent",
                "duration_hours": 1.0,
            },
            {
                "date": datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC).date(),
                "time": datetime(2023, 7, 1, 10, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
                "summary": "Second",
                "duration_hours": 1.0,
            },
            {
                "date": datetime(2023, 7, 3, tzinfo=calendar_analyzer.PACIFIC).date(),
                "time": datetime(2023, 7, 1, 11, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
                "summary": "Third",
                "duration_hours": 1.0,
            },
        ]
    )

    result = calendar_analyzer.generate_summary(
        meetings,
        calendar_analyzer.SummaryOptions(num_titles=2, num_times=1),
    )

    assert "Top 1 Most Common Meeting Times" in result
    assert "- 09:00 AM: 2 meetings" in result
    assert "- 10:00 AM: 1 meetings" not in result
    assert "Top 2 Most Frequent Meeting Titles" in result
    assert "Frequent" in result
    assert "Second" in result
    assert "Third" not in result


def test_generate_summary_uses_requested_period_for_average() -> None:
    """Test summary averages use the requested period rather than meeting spread."""
    meetings = meeting_frame(
        [
            {
                "date": datetime(2023, 7, 5, tzinfo=calendar_analyzer.PACIFIC).date(),
                "time": datetime(2023, 7, 5, 10, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
                "summary": "Middle Meeting",
                "duration_hours": 1.0,
            }
        ]
    )

    result = calendar_analyzer.generate_summary(
        meetings,
        calendar_analyzer.SummaryOptions(
            period_start=datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            period_end=datetime(2023, 7, 11, tzinfo=calendar_analyzer.PACIFIC),
        ),
    )

    assert "- From: July 01, 2023" in result
    assert "- To:   July 11, 2023" in result
    assert "- Span: 11 days" in result
    assert "- Average Meetings per Day: 0.1" in result


def test_generate_summary_shows_imported_coverage_and_query_range() -> None:
    """Test summary distinguishes imported data coverage from the query range."""
    meetings = meeting_frame(
        [
            {
                "date": datetime(2023, 7, 5, tzinfo=calendar_analyzer.PACIFIC).date(),
                "time": datetime(2023, 7, 5, 10, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
                "summary": "Middle Meeting",
                "duration_hours": 1.0,
            }
        ]
    )

    result = calendar_analyzer.generate_summary(
        meetings,
        calendar_analyzer.SummaryOptions(
            period_start=datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            period_end=datetime(2023, 7, 31, tzinfo=calendar_analyzer.PACIFIC),
            data_start=datetime(2023, 1, 1, tzinfo=calendar_analyzer.PACIFIC).date(),
            data_end=datetime(2023, 12, 31, tzinfo=calendar_analyzer.PACIFIC).date(),
        ),
    )

    assert "Imported Data Coverage:" in result
    assert "- From: January 01, 2023" in result
    assert "- To:   December 31, 2023" in result
    assert "Query Date Range:" in result
    assert "- From: July 01, 2023" in result
    assert "- To:   July 31, 2023" in result


def test_analyze_calendar_date_filtering() -> None:
    """Test that date filtering works correctly."""
    tmp_path = create_temp_sqlite_calendar(
        [
            (
                "Before Range",
                datetime(2023, 6, 15, 17, 0, tzinfo=UTC),
                datetime(2023, 6, 15, 18, 0, tzinfo=UTC),
                0,
            ),
            (
                "In Range",
                datetime(2023, 7, 1, 17, 0, tzinfo=UTC),
                datetime(2023, 7, 1, 18, 0, tzinfo=UTC),
                0,
            ),
            (
                "After Range",
                datetime(2023, 7, 15, 17, 0, tzinfo=UTC),
                datetime(2023, 7, 15, 18, 0, tzinfo=UTC),
                0,
            ),
        ]
    )

    meetings, stats = analysis_result(
        calendar_analyzer.analyze_calendar(
            Path(tmp_path),
            datetime(2023, 6, 30, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 5, tzinfo=calendar_analyzer.PACIFIC),
        )
    )

    assert stats["total_meetings"] == 1
    assert meetings[0]["summary"] == "In Range"

    Path(tmp_path).unlink()


def test_get_calendar_path_with_specified_file(capsys) -> None:
    """Test get_calendar_path when a specific file is provided."""
    tmp_path = create_temp_sqlite_calendar()

    try:
        result = calendar_analyzer.get_calendar_path(tmp_path)

        assert result == Path(tmp_path).resolve()

        out = capsys.readouterr().out
        assert f"Looking for calendar at: {Path(tmp_path).resolve()}" in out
        assert "Path exists: True" in out
        assert "Is directory: False" in out
    finally:
        Path(tmp_path).unlink()


def test_get_calendar_path_with_directory(capsys) -> None:
    """Test get_calendar_path when a directory is provided."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create some files in the directory
        test_file = Path(tmp_dir) / "test.txt"
        test_file.write_text("test")

        result = calendar_analyzer.get_calendar_path(tmp_dir)

        assert result == Path(tmp_dir).resolve()

        out = capsys.readouterr().out
        assert f"Looking for calendar at: {Path(tmp_dir).resolve()}" in out
        assert "Path exists: True" in out
        assert "Is directory: True" in out
        assert "Directory contents:" in out
        assert "test.txt" in out


def test_get_calendar_path_nonexistent_file(capsys) -> None:
    """Test get_calendar_path with a nonexistent file."""
    # Use a more secure temporary path that doesn't exist
    nonexistent_path = create_temp_dummy_file("_nonexistent.sqlitedb")
    # Remove the file to make it nonexistent but keep the secure path
    Path(nonexistent_path).unlink()

    result = calendar_analyzer.get_calendar_path(nonexistent_path)

    assert result == Path(nonexistent_path).resolve()

    out = capsys.readouterr().out
    assert f"Looking for calendar at: {Path(nonexistent_path).resolve()}" in out
    assert "Path exists: False" in out


def test_get_calendar_path_oserror(capsys) -> None:
    """Test get_calendar_path when OSError occurs."""
    with patch("pathlib.Path.resolve", side_effect=OSError("Permission denied")):
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.get_calendar_path("/some/path")

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Error processing path: Permission denied" in out


@patch("pathlib.Path.home")
def test_get_calendar_path_auto_discovery_with_files(mock_home, capsys) -> None:
    """Test auto-discovery when calendar files are found."""
    # Create a temporary directory structure
    with tempfile.TemporaryDirectory() as tmp_dir:
        home_path = Path(tmp_dir)
        mock_home.return_value = home_path

        # Create directory structure
        documents_dir = home_path / "Documents"
        documents_dir.mkdir()

        # Create calendar files with different timestamps
        old_calendar = documents_dir / "old_calendar.olm"
        new_calendar = documents_dir / "new_calendar.olm"

        old_calendar.write_text("old calendar content")
        new_calendar.write_text("new calendar content")

        # Make old_calendar older by changing its modification time
        old_time = new_calendar.stat().st_mtime - 3600  # 1 hour ago
        os.utime(old_calendar, (old_time, old_time))

        result = calendar_analyzer.get_calendar_path()

        # Should return the newer file
        assert result == new_calendar

        out = capsys.readouterr().out
        assert "Searching for calendar files in:" in out
        assert "✓ Directory exists" in out
        assert "✓ Found 2 calendar files" in out
        assert f"Selected most recent calendar file: {new_calendar}" in out


@patch("pathlib.Path.home")
def test_get_calendar_path_auto_discovery_ignores_csv_files(mock_home, capsys) -> None:
    """Test auto-discovery ignores CSV files unless explicitly requested."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home_path = Path(tmp_dir)
        mock_home.return_value = home_path
        documents_dir = home_path / "Documents"
        documents_dir.mkdir()
        (documents_dir / "export.csv").write_text("Subject,Start Date,Start Time\nMeeting,07/01/2023,10:00 AM\n")

        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.get_calendar_path()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "✗ No calendar files found" in out
        assert "export.csv" not in out


@patch("pathlib.Path.home")
def test_get_calendar_path_auto_discovery_no_files(mock_home, capsys) -> None:
    """Test auto-discovery when no calendar files are found."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home_path = Path(tmp_dir)
        mock_home.return_value = home_path

        # Create empty directories
        for subdir in ["Documents", "Downloads"]:
            (home_path / subdir).mkdir()

        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.get_calendar_path()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Searching for calendar files in:" in out
        assert "✗ No calendar files found" in out
        assert "Error: No calendar files found in any of the expected locations." in out
        assert "Please export your calendar from Calendar or Outlook:" in out


@patch("pathlib.Path.home")
def test_get_calendar_path_auto_discovery_nonexistent_dirs(mock_home, capsys) -> None:
    """Test auto-discovery when directories don't exist."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home_path = Path(tmp_dir)
        mock_home.return_value = home_path

        # Don't create any subdirectories

        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.get_calendar_path()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Searching for calendar files in:" in out
        assert "✗ Directory does not exist" in out
        assert "Error: No calendar files found in any of the expected locations." in out


@patch("pathlib.Path.home")
def test_get_calendar_path_auto_discovery_multiple_file_types(mock_home, capsys) -> None:
    """Test auto-discovery with multiple calendar file types."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home_path = Path(tmp_dir)
        mock_home.return_value = home_path

        # Create directory structure
        library_dir = home_path / "Library" / "Calendars"
        library_dir.mkdir(parents=True)
        documents_dir = home_path / "Documents"
        documents_dir.mkdir()

        # Create different types of calendar files
        pst_file = documents_dir / "calendar.pst"
        icbu_file = library_dir / "backup.icbu"
        sqlite_file = library_dir / "calendar.sqlitedb"
        olm_file = documents_dir / "calendar.olm"

        pst_file.write_text("pst content")
        icbu_file.write_text("icbu content")
        sqlite_file.write_text("sqlite content")
        olm_file.write_text("olm content")

        result = calendar_analyzer.get_calendar_path()

        # Should find one of the files (the most recent one)
        assert result.exists()
        assert result.suffix in [".icbu", ".sqlitedb", ".olm", ".pst"]

        out = capsys.readouterr().out
        # The function prints found files per directory, not total
        assert out.count("✓ Found 2 calendar files") == 2


@patch("pathlib.Path.home")
def test_get_calendar_path_auto_discovery_many_files(mock_home, capsys) -> None:
    """Test auto-discovery with many calendar files (tests truncation)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home_path = Path(tmp_dir)
        mock_home.return_value = home_path

        # Create directory structure
        documents_dir = home_path / "Documents"
        documents_dir.mkdir()

        # Create 7 calendar files
        for i in range(7):
            calendar_file = documents_dir / f"calendar_{i}.olm"
            calendar_file.write_text(f"calendar {i} content")

        result = calendar_analyzer.get_calendar_path()

        assert result.exists()

        out = capsys.readouterr().out
        assert "✓ Found 7 calendar files" in out
        assert "... and 2 more" in out  # Should show first 5 + "... and 2 more"


@patch("pathlib.Path.home")
def test_get_calendar_path_auto_discovery_subdirectories(mock_home, capsys) -> None:
    """Test auto-discovery finds files in subdirectories."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home_path = Path(tmp_dir)
        mock_home.return_value = home_path

        # Create nested directory structure
        nested_dir = home_path / "Documents" / "Calendars" / "Exports"
        nested_dir.mkdir(parents=True)

        # Create calendar file in nested directory
        nested_calendar = nested_dir / "nested_calendar.olm"
        nested_calendar.write_text("nested calendar content")

        result = calendar_analyzer.get_calendar_path()

        assert result == nested_calendar

        out = capsys.readouterr().out
        assert "✓ Found 1 calendar files" in out
        assert f"Selected most recent calendar file: {nested_calendar}" in out


def test_analyze_calendar_icbu_with_sqlite(capsys) -> None:
    """Test ICBU file handling with SQLite database inside."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        icbu_path = Path(tmp_dir) / "backup.icbu"
        icbu_path.mkdir()

        sqlite_path = icbu_path / "Calendar.sqlitedb"
        with closing(sqlite3.connect(sqlite_path)) as conn:
            conn.execute("CREATE TABLE CalendarItem (summary TEXT, start_date INTEGER, end_date INTEGER)")
            conn.commit()

        result = calendar_analyzer.analyze_calendar(icbu_path)

        assert result.is_empty()
        out = capsys.readouterr().out
        assert f"Found SQLite database in ICBU backup: {sqlite_path}" in out


def test_analyze_calendar_with_sqlite_file(capsys) -> None:
    """Test direct SQLite calendar analysis through analyze_calendar."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sqlite_path = Path(tmp_dir) / "calendar.sqlitedb"
        start_dt = datetime(2023, 7, 1, 17, 0, tzinfo=UTC)
        end_dt = datetime(2023, 7, 1, 18, 30, tzinfo=UTC)
        start_seconds = int((start_dt - calendar_analyzer.APPLE_EPOCH).total_seconds())
        end_seconds = int((end_dt - calendar_analyzer.APPLE_EPOCH).total_seconds())

        with closing(sqlite3.connect(sqlite_path)) as conn:
            conn.execute("CREATE TABLE CalendarItem (summary TEXT, start_date INTEGER, end_date INTEGER)")
            conn.execute(
                "INSERT INTO CalendarItem (summary, start_date, end_date) VALUES (?, ?, ?)",
                ("SQLite Meeting", start_seconds, end_seconds),
            )
            conn.commit()

        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                sqlite_path,
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )

        assert stats == {"total_meetings": 1, "total_hours": 1.5}
        assert meetings[0]["summary"] == "SQLite Meeting"
        assert meetings[0]["time"].hour == 10
        assert capsys.readouterr().out == ""


def test_analyze_calendar_with_sqlite_file_honors_days_back() -> None:
    """Test SQLite calendar analysis honors the requested days-back window."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sqlite_path = Path(tmp_dir) / "calendar.sqlitedb"
        start_dt = datetime(2023, 6, 1, 17, 0, tzinfo=UTC)
        end_dt = datetime(2023, 6, 1, 18, 0, tzinfo=UTC)
        start_seconds = int((start_dt - calendar_analyzer.APPLE_EPOCH).total_seconds())
        end_seconds = int((end_dt - calendar_analyzer.APPLE_EPOCH).total_seconds())

        with closing(sqlite3.connect(sqlite_path)) as conn:
            conn.execute("CREATE TABLE CalendarItem (summary TEXT, start_date INTEGER, end_date INTEGER)")
            conn.execute(
                "INSERT INTO CalendarItem (summary, start_date, end_date) VALUES (?, ?, ?)",
                ("Outside Window", start_seconds, end_seconds),
            )
            conn.commit()

        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                sqlite_path,
                end_date=datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                days_back=1,
            )
        )

        assert meetings == []
        assert stats == {"total_meetings": 0, "total_hours": 0.0}


def test_analyze_calendar_skips_sqlite_all_day_rows() -> None:
    """Test SQLite calendar analysis excludes all-day-like rows."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sqlite_path = Path(tmp_dir) / "calendar.sqlitedb"
        meeting_start = datetime(2023, 7, 1, 17, 0, tzinfo=UTC)
        meeting_end = datetime(2023, 7, 1, 18, 0, tzinfo=UTC)
        all_day_start = datetime(2023, 7, 1, 7, 0, tzinfo=UTC)
        all_day_end = datetime(2023, 7, 2, 7, 0, tzinfo=UTC)
        midnight_start = datetime(2023, 7, 2, 7, 0, tzinfo=UTC)
        midnight_end = datetime(2023, 7, 2, 8, 0, tzinfo=UTC)
        workday_start = datetime(2023, 7, 3, 16, 0, tzinfo=UTC)
        workday_end = datetime(2023, 7, 4, 0, 0, tzinfo=UTC)

        with closing(sqlite3.connect(sqlite_path)) as conn:
            conn.execute(
                "CREATE TABLE CalendarItem (summary TEXT, start_date INTEGER, end_date INTEGER, all_day INTEGER)"
            )
            conn.executemany(
                "INSERT INTO CalendarItem (summary, start_date, end_date, all_day) VALUES (?, ?, ?, ?)",
                [
                    (
                        "Timed Meeting",
                        int((meeting_start - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                        int((meeting_end - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                        0,
                    ),
                    (
                        "All Day Event",
                        int((all_day_start - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                        int((all_day_end - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                        1,
                    ),
                    (
                        "Midnight Export Block",
                        int((midnight_start - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                        int((midnight_end - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                        0,
                    ),
                    (
                        "Workday Block",
                        int((workday_start - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                        int((workday_end - calendar_analyzer.APPLE_EPOCH).total_seconds()),
                        0,
                    ),
                ],
            )
            conn.commit()

        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                sqlite_path,
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )

        assert stats == {"total_meetings": 1, "total_hours": 1.0}
        assert [meeting["summary"] for meeting in meetings] == ["Timed Meeting"]


def test_analyze_calendar_with_olm_file() -> None:
    """Test direct Outlook for Mac OLM calendar analysis through analyze_calendar."""
    calendar_xml = textwrap.dedent("""
    <appointments>
      <appointment>
        <OPFCalendarEventCopySummary>Outlook Mac Meeting</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-07-01T17:00:00Z</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndTime>2023-07-01T18:30:00Z</OPFCalendarEventCopyEndTime>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>Outlook All Day</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-07-01T00:00:00Z</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyIsAllDayEvent>true</OPFCalendarEventCopyIsAllDayEvent>
      </appointment>
    </appointments>
    """)
    tmp_path = create_temp_olm_file(calendar_xml)

    try:
        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                Path(tmp_path),
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.5}
    assert meetings[0]["summary"] == "Outlook Mac Meeting"
    assert meetings[0]["time"].hour == 10


def test_analyze_calendar_defaults_missing_olm_end_and_summary() -> None:
    """Test OLM calendar analysis defaults optional appointment fields."""
    calendar_xml = textwrap.dedent("""
    <appointments>
      <appointment>
        <OPFCalendarEventCopyStartTime>2023-07-01T10:00:00</OPFCalendarEventCopyStartTime>
      </appointment>
    </appointments>
    """)
    tmp_path = create_temp_olm_file(calendar_xml)

    try:
        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                Path(tmp_path),
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert meetings[0]["summary"] == "No Title"


def test_analyze_calendar_treats_olm_iso_without_timezone_as_utc() -> None:
    """Test OLM combined ISO timestamps without Z are interpreted as UTC."""
    calendar_xml = textwrap.dedent("""
    <appointments>
      <appointment>
        <OPFCalendarEventCopySummary>OLM UTC-ish Meeting</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-07-01T17:00:00</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndTime>2023-07-01T18:30:00</OPFCalendarEventCopyEndTime>
      </appointment>
    </appointments>
    """)
    tmp_path = create_temp_olm_file(calendar_xml)

    try:
        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                Path(tmp_path),
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.5}
    assert meetings[0]["summary"] == "OLM UTC-ish Meeting"
    assert meetings[0]["time"].hour == 10


def test_analyze_calendar_filters_requested_olm_range() -> None:
    """Test OLM calendar analysis only includes appointments in the requested range."""
    calendar_xml = textwrap.dedent("""
    <appointments>
      <appointment>
        <OPFCalendarEventCopySummary>Before Range</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-06-30T17:00:00Z</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndTime>2023-06-30T18:00:00Z</OPFCalendarEventCopyEndTime>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>In Range</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-07-03T17:00:00Z</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndTime>2023-07-03T18:00:00Z</OPFCalendarEventCopyEndTime>
      </appointment>
    </appointments>
    """)
    tmp_path = create_temp_olm_file(calendar_xml)

    try:
        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                Path(tmp_path),
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 4, tzinfo=calendar_analyzer.PACIFIC),
            )
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["In Range"]


def test_analyze_calendar_skips_olm_all_day_and_date_only_appointments() -> None:
    """Test OLM analysis excludes all-day-like appointments."""
    calendar_xml = textwrap.dedent("""
    <appointments>
      <appointment>
        <OPFCalendarEventCopySummary>Split Timed Meeting</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartDate>2023-07-01</OPFCalendarEventCopyStartDate>
        <OPFCalendarEventCopyStartTime>10:00 AM</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndDate>2023-07-01</OPFCalendarEventCopyEndDate>
        <OPFCalendarEventCopyEndTime>11:00 AM</OPFCalendarEventCopyEndTime>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>Date Only Event</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartDate>2023-07-01</OPFCalendarEventCopyStartDate>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>Explicit All Day Event</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartDate>2023-07-01</OPFCalendarEventCopyStartDate>
        <OPFCalendarEventGetIsAllDayEvent>1</OPFCalendarEventGetIsAllDayEvent>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>Free Calendar Hold</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-07-01T12:00:00</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndTime>2023-07-01T13:00:00</OPFCalendarEventCopyEndTime>
        <OPFCalendarEventCopyFreeBusyStatus>0</OPFCalendarEventCopyFreeBusyStatus>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>Out Of Office Hold</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-07-01T17:00:00Z</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndTime>2023-07-01T18:00:00Z</OPFCalendarEventCopyEndTime>
        <OPFCalendarEventCopyFreeBusyStatus>3</OPFCalendarEventCopyFreeBusyStatus>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>UTC Midnight Default End</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-07-02T00:00:00Z</OPFCalendarEventCopyStartTime>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>Midnight Export Block</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>2023-07-02T00:00:00</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndTime>2023-07-02T01:00:00</OPFCalendarEventCopyEndTime>
      </appointment>
      <appointment>
        <OPFCalendarEventCopySummary>Workday Block</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartDate>2023-07-03</OPFCalendarEventCopyStartDate>
        <OPFCalendarEventCopyStartTime>9:00 AM</OPFCalendarEventCopyStartTime>
        <OPFCalendarEventCopyEndDate>2023-07-03</OPFCalendarEventCopyEndDate>
        <OPFCalendarEventCopyEndTime>5:00 PM</OPFCalendarEventCopyEndTime>
      </appointment>
    </appointments>
    """)
    tmp_path = create_temp_olm_file(calendar_xml)

    try:
        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                Path(tmp_path),
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["Split Timed Meeting"]
    assert meetings[0]["time"].hour == 10


def test_analyze_calendar_without_olm_calendar_xml(capsys) -> None:
    """Test OLM calendar analysis reports archives without Calendar.xml."""
    with tempfile.NamedTemporaryFile(suffix=".olm", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    with zipfile.ZipFile(tmp_path, "w") as archive:
        archive.writestr("Accounts/Mail.xml", "<emails />")

    try:
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_calendar(Path(tmp_path))
    finally:
        tmp_path.unlink()

    assert exc_info.value.code == 1
    assert "Error parsing OLM calendar: No Calendar.xml entries found in OLM archive." in capsys.readouterr().out


def test_analyze_calendar_bad_olm_archive(capsys) -> None:
    """Test OLM calendar analysis reports unreadable OLM archives."""
    with tempfile.NamedTemporaryFile(suffix=".olm", delete=False) as tmp:
        tmp.write(b"not a zip archive")
        tmp_path = Path(tmp.name)

    try:
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_calendar(Path(tmp_path))
    finally:
        tmp_path.unlink()

    assert exc_info.value.code == 1
    assert "Error reading OLM calendar:" in capsys.readouterr().out


def test_analyze_calendar_bad_olm_xml(capsys) -> None:
    """Test OLM calendar analysis reports malformed Calendar.xml content."""
    with tempfile.NamedTemporaryFile(suffix=".olm", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    with zipfile.ZipFile(tmp_path, "w") as archive:
        archive.writestr("Accounts/Calendar.xml", "<appointments><appointment></appointments>")

    try:
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_calendar(Path(tmp_path))
    finally:
        tmp_path.unlink()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing OLM calendar: Accounts/Calendar.xml is not valid XML:" in out


def test_analyze_calendar_errors_when_no_olm_start_dates_parse(capsys) -> None:
    """Test OLM calendar analysis reports unsupported date formats."""
    calendar_xml = textwrap.dedent("""
    <appointments>
      <appointment>
        <OPFCalendarEventCopySummary>Broken Date</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartTime>not-a-date</OPFCalendarEventCopyStartTime>
      </appointment>
    </appointments>
    """)
    tmp_path = create_temp_olm_file(calendar_xml)

    try:
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_calendar(Path(tmp_path))
    finally:
        Path(tmp_path).unlink()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing OLM calendar: Could not parse any OLM appointment start dates." in out
    assert "not-a-date" in out


def test_analyze_calendar_errors_when_olm_split_start_time_invalid(capsys) -> None:
    """Test split OLM dates with invalid times are not mistaken for all-day events."""
    calendar_xml = textwrap.dedent("""
    <appointments>
      <appointment>
        <OPFCalendarEventCopySummary>Broken Split Date</OPFCalendarEventCopySummary>
        <OPFCalendarEventCopyStartDate>2023-07-01</OPFCalendarEventCopyStartDate>
        <OPFCalendarEventCopyStartTime>not-a-time</OPFCalendarEventCopyStartTime>
      </appointment>
    </appointments>
    """)
    tmp_path = create_temp_olm_file(calendar_xml)

    try:
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_calendar(Path(tmp_path))
    finally:
        Path(tmp_path).unlink()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing OLM calendar: Could not parse any OLM appointment start dates." in out
    assert "not-a-time" in out


def test_analyze_calendar_with_explicit_outlook_csv_file() -> None:
    """Test explicit Outlook CSV calendar analysis remains supported."""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", newline="", delete=False) as tmp:
        tmp.write("Subject,Start Date,Start Time,End Date,End Time,All day event\n")
        tmp.write("CSV Meeting,07/01/2023,10:00 AM,07/01/2023,11:30 AM,False\n")
        tmp.write("CSV All Day,07/01/2023,12:00 AM,07/01/2023,11:59 PM,True\n")
        tmp_path = Path(tmp.name)

    try:
        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                tmp_path,
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )
    finally:
        tmp_path.unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.5}
    assert meetings[0]["summary"] == "CSV Meeting"
    assert meetings[0]["time"].hour == 10


def test_analyze_calendar_outlook_csv_read_error(capsys, tmp_path: Path) -> None:
    """Test Outlook CSV analysis reports unreadable files."""
    missing_path = tmp_path / "missing.csv"

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_calendar(missing_path)

    assert exc_info.value.code == 1
    assert "Error reading Outlook CSV calendar:" in capsys.readouterr().out


def test_analyze_calendar_outlook_csv_requires_header(capsys, tmp_path: Path) -> None:
    """Test Outlook CSV analysis reports an empty CSV file."""
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_calendar(csv_path)

    assert exc_info.value.code == 1
    assert "Error parsing Outlook CSV calendar: CSV header row is missing." in capsys.readouterr().out


def test_analyze_calendar_outlook_csv_requires_start_columns(capsys, tmp_path: Path) -> None:
    """Test Outlook CSV analysis rejects non-calendar CSV files."""
    csv_path = tmp_path / "not-calendar.csv"
    csv_path.write_text("Amount,Description\n1.00,Coffee\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_calendar(csv_path)

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing Outlook CSV calendar:" in out
    assert "CSV must include Outlook start columns" in out


def test_analyze_calendar_outlook_csv_errors_when_timed_starts_do_not_parse(capsys, tmp_path: Path) -> None:
    """Test Outlook CSV analysis reports timed-looking rows with unsupported start dates."""
    csv_path = tmp_path / "calendar.csv"
    csv_path.write_text(
        textwrap.dedent("""\
        Subject,Start Date,Start Time,End Date,End Time
        Broken Start,not-a-date,10:00 AM,07/01/2023,11:00 AM
        Another Broken Start,07/02/2023,not-a-time,07/02/2023,11:00 AM
        """),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_calendar(csv_path)

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing Outlook CSV calendar: Could not parse any Outlook CSV start dates." in out
    assert "not-a-date 10:00 AM" in out
    assert "07/02/2023 not-a-time" in out


def test_analyze_calendar_outlook_csv_errors_when_combined_start_does_not_parse(
    capsys,
    tmp_path: Path,
) -> None:
    """Test Outlook CSV analysis reports unsupported combined start values."""
    csv_path = tmp_path / "calendar.csv"
    csv_path.write_text(
        textwrap.dedent("""\
        Subject,Start,End
        Broken Combined Start,not-a-date 10:00 AM,2023-07-01 11:00:00
        """),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_calendar(csv_path)

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing Outlook CSV calendar: Could not parse any Outlook CSV start dates." in out
    assert "not-a-date 10:00 AM" in out


def test_analyze_calendar_filters_requested_outlook_csv_range(tmp_path: Path) -> None:
    """Test Outlook CSV analysis only includes rows in the requested range."""
    csv_path = tmp_path / "calendar.csv"
    csv_path.write_text(
        textwrap.dedent("""\
        Subject,Start Date,Start Time,End Date,End Time
        Before Range,06/30/2023,10:00 AM,06/30/2023,11:00 AM
        In Range,07/03/2023,10:00 AM,07/03/2023,11:00 AM
        """),
        encoding="utf-8",
    )

    meetings, stats = analysis_result(
        calendar_analyzer.analyze_calendar(
            csv_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 4, tzinfo=calendar_analyzer.PACIFIC),
        )
    )

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["In Range"]


def test_analyze_calendar_skips_outlook_csv_date_only_rows() -> None:
    """Test Outlook CSV all-day-like rows are excluded."""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", newline="", delete=False) as tmp:
        tmp.write("Subject,Start Date,Start Time,End Date,End Time,Show Time As\n")
        tmp.write("CSV Timed Meeting,07/01/2023,10:00 AM,07/01/2023,11:00 AM,Busy\n")
        tmp.write("CSV Date Only Event,07/01/2023,,07/01/2023,,Busy\n")
        tmp.write("CSV Free Hold,07/01/2023,12:00 PM,07/01/2023,1:00 PM,Free\n")
        tmp.write("CSV Out Of Office Hold,07/01/2023,5:00 PM,07/01/2023,6:00 PM,Out of Office\n")
        tmp.write("CSV Midnight Export Block,07/02/2023,12:00 AM,07/02/2023,1:00 AM,Busy\n")
        tmp.write("CSV Workday Block,07/03/2023,9:00 AM,07/03/2023,5:00 PM,Busy\n")
        tmp_path = Path(tmp.name)

    try:
        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                tmp_path,
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )
    finally:
        tmp_path.unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["CSV Timed Meeting"]


def test_analyze_calendar_skips_outlook_csv_combined_date_only_start() -> None:
    """Test combined Outlook Start columns need a time component."""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", newline="", delete=False) as tmp:
        tmp.write("Subject,Start,End\n")
        tmp.write("CSV Combined Timed,2023-07-01 10:00:00,2023-07-01 11:00:00\n")
        tmp.write("CSV Combined Date Only,2023-07-01,2023-07-01\n")
        tmp_path = Path(tmp.name)

    try:
        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                tmp_path,
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )
    finally:
        tmp_path.unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["CSV Combined Timed"]


def test_analyze_calendar_outlook_csv_parses_iso_datetime_variants(tmp_path: Path) -> None:
    """Test Outlook CSV parsing accepts naive and timezone-aware ISO datetimes."""
    csv_path = tmp_path / "calendar.csv"
    csv_path.write_text(
        textwrap.dedent("""\
        Subject,Start,End
        Naive ISO,2023-07-01T10:30:00,2023-07-01T11:30:00
        UTC ISO,2023-07-01T17:30:00+00:00,2023-07-01T18:30:00+00:00
        """),
        encoding="utf-8",
    )

    meetings, stats = analysis_result(
        calendar_analyzer.analyze_calendar(
            csv_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )
    )

    assert stats == {"total_meetings": 2, "total_hours": 2.0}
    assert [meeting["summary"] for meeting in meetings] == ["Naive ISO", "UTC ISO"]
    assert [meeting["time"].hour for meeting in meetings] == [10, 10]


def test_analyze_calendar_outlook_csv_defaults_malformed_split_end(tmp_path: Path) -> None:
    """Test malformed split end columns fall back to the default duration."""
    csv_path = tmp_path / "calendar.csv"
    csv_path.write_text(
        textwrap.dedent("""\
        Subject,Start Date,Start Time,End Date,End Time
        Default End,07/01/2023,10:00 AM,not-a-date,
        """),
        encoding="utf-8",
    )

    meetings, stats = analysis_result(
        calendar_analyzer.analyze_calendar(
            csv_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )
    )

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["Default End"]


def test_analyze_calendar_defaults_missing_sqlite_summary() -> None:
    """Test SQLite rows use the default title when summary is empty."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sqlite_path = Path(tmp_dir) / "calendar.sqlitedb"
        start_dt = datetime(2023, 7, 1, 17, 0, tzinfo=UTC)
        end_dt = datetime(2023, 7, 1, 18, 0, tzinfo=UTC)
        start_seconds = int((start_dt - calendar_analyzer.APPLE_EPOCH).total_seconds())
        end_seconds = int((end_dt - calendar_analyzer.APPLE_EPOCH).total_seconds())

        with closing(sqlite3.connect(sqlite_path)) as conn:
            conn.execute("CREATE TABLE CalendarItem (summary TEXT, start_date INTEGER, end_date INTEGER)")
            conn.execute(
                "INSERT INTO CalendarItem (summary, start_date, end_date) VALUES (?, ?, ?)",
                (None, start_seconds, end_seconds),
            )
            conn.commit()

        meetings, stats = analysis_result(
            calendar_analyzer.analyze_calendar(
                sqlite_path,
                datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
            )
        )

        assert stats == {"total_meetings": 1, "total_hours": 1.0}
        assert meetings[0]["summary"] == "No Title"


def test_analyze_calendar_sqlite_read_error(capsys) -> None:
    """Test SQLite analysis reports database read errors."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sqlite_path = Path(tmp_dir) / "calendar.sqlitedb"
        sqlite_path.write_text("not a sqlite database")

        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_calendar(sqlite_path)

        assert exc_info.value.code == 1
        assert "Error reading SQLite calendar:" in capsys.readouterr().out


def test_analyze_calendar_icbu_no_calendar_data(capsys) -> None:
    """Test ICBU file handling when no calendar data is found."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        icbu_path = Path(tmp_dir) / "backup.icbu"
        icbu_path.mkdir()

        # Create some non-calendar files
        (icbu_path / "other_file.txt").write_text("not a calendar")
        (icbu_path / "metadata.plist").write_text("some metadata")

        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_calendar(icbu_path)

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert f"Error: Could not find Calendar.sqlitedb in ICBU backup: {icbu_path}" in out
        assert "Contents of ICBU directory:" in out
        assert "other_file.txt" in out
        assert "metadata.plist" in out


def test_analyze_calendar_icbu_directory_listing_error(capsys) -> None:
    """Test ICBU directory listing error handling."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        icbu_path = Path(tmp_dir) / "backup.icbu"
        icbu_path.mkdir()

        # Mock iterdir to raise OSError
        with patch.object(Path, "iterdir", side_effect=OSError("Permission denied")):
            with pytest.raises(SystemExit) as exc_info:
                calendar_analyzer.analyze_calendar(icbu_path)

            assert exc_info.value.code == 1
            out = capsys.readouterr().out
            assert "Error listing directory contents: Permission denied" in out


def test_analyze_calendar_requires_windows_for_pst_import(capsys, tmp_path: Path) -> None:
    """Test PST files are accepted but require Windows classic Outlook."""
    pst_path = tmp_path / "calendar.pst"
    pst_path.write_bytes(b"not an Outlook calendar export")

    with patch.object(calendar_analyzer.sys, "platform", "darwin"), pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.import_calendar_meetings(pst_path)

    assert exc_info.value.code == 1
    assert (
        "Outlook PST import is only available on Windows with classic Microsoft Outlook installed"
        in capsys.readouterr().out
    )


def test_analyze_calendar_rejects_ics_files(capsys, tmp_path: Path) -> None:
    """Test direct iCal/ICS files are no longer supported."""
    calendar_path = tmp_path / "calendar.ics"
    calendar_path.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.import_calendar_meetings(calendar_path)

    assert exc_info.value.code == 1
    assert "ICS/iCal files are no longer supported" in capsys.readouterr().out


def test_analyze_calendar_default_date_range() -> None:
    """Test analyze_calendar with default date ranges (no start/end specified)."""
    # Use a recent date that would be within the default 365-day range
    recent_date = datetime.now(UTC) - timedelta(days=30)  # 30 days ago
    tmp_path = create_temp_sqlite_calendar([("Recent Meeting", recent_date, recent_date + timedelta(hours=1), 0)])

    meetings, stats = analysis_result(calendar_analyzer.analyze_calendar(Path(tmp_path)))

    assert isinstance(stats, dict)
    assert stats["total_meetings"] == 1
    assert stats["total_hours"] == 1.0
    assert meetings[0]["summary"] == "Recent Meeting"

    Path(tmp_path).unlink()


class OutlookItems:
    """Minimal Outlook Items collection test double."""

    def __init__(self, items: list[object]) -> None:
        """Store fake Outlook items."""
        self._items = items
        self.Count = len(items)
        self.IncludeRecurrences = False

    def Sort(self, _field: str) -> None:  # noqa: N802
        """Pretend to sort Outlook appointments."""

    def Item(self, index: int) -> object:  # noqa: N802
        """Return a one-indexed Outlook item."""
        return self._items[index - 1]


class OutlookAppointment:
    """Minimal Outlook appointment item test double."""

    Class = calendar_analyzer.OUTLOOK_APPOINTMENT_CLASS
    MessageClass = "IPM.Appointment"

    def __init__(
        self,
        subject: str,
        start: datetime,
        end: datetime,
        *,
        all_day: bool = False,
        busy_status: int = 2,
    ) -> None:
        """Store fake Outlook appointment fields."""
        self.Subject = subject
        self.Start = start
        self.End = end
        self.AllDayEvent = all_day
        self.BusyStatus = busy_status


class UnreadableOutlookItems:
    """Outlook Items test double that fails if a non-calendar folder is read."""

    @property
    def Count(self) -> int:  # noqa: N802
        """Fail when code tries to enumerate non-calendar folder items."""
        message = "non-calendar folder items should not be enumerated"
        raise AssertionError(message)

    def Sort(self, _field: str) -> None:  # noqa: N802
        """Fail when code tries to sort non-calendar folder items."""
        message = "non-calendar folder items should not be sorted"
        raise AssertionError(message)

    def Item(self, _index: int) -> object:  # noqa: N802
        """Fail when code tries to fetch non-calendar folder items."""
        message = "non-calendar folder items should not be read"
        raise AssertionError(message)


class OutlookFolder:
    """Minimal Outlook folder tree test double."""

    def __init__(
        self,
        items: list[object],
        folders: list[object] | None = None,
        *,
        default_item_type: int = calendar_analyzer.OUTLOOK_APPOINTMENT_ITEM_TYPE,
    ) -> None:
        """Store fake Outlook folder contents."""
        self.Items: object = OutlookItems(items)
        self.Folders = OutlookItems(folders or [])
        self.DefaultItemType = default_item_type


class OutlookStore:
    """Minimal Outlook Store test double."""

    def __init__(self, file_path: str, root_folder: OutlookFolder) -> None:
        """Store fake Outlook store fields."""
        self.FilePath = file_path
        self.StoreID = f"store:{file_path}"
        self._root_folder = root_folder

    def GetRootFolder(self) -> OutlookFolder:  # noqa: N802
        """Return the fake root folder."""
        return self._root_folder


class OutlookStores:
    """Minimal Outlook Stores collection test double."""

    def __init__(self) -> None:
        """Create an empty store collection."""
        self._stores: list[OutlookStore] = []

    @property
    def Count(self) -> int:  # noqa: N802
        """Return the Outlook-style store count."""
        return len(self._stores)

    def Item(self, index: int) -> OutlookStore:  # noqa: N802
        """Return a one-indexed Outlook store."""
        return self._stores[index - 1]

    def append(self, store: OutlookStore) -> None:
        """Add a fake Outlook store."""
        self._stores.append(store)

    def remove_root(self, root_folder: OutlookFolder) -> None:
        """Remove the store with the given root folder."""
        self._stores = [store for store in self._stores if store.GetRootFolder() is not root_folder]


class OutlookNamespace:
    """Minimal Outlook MAPI namespace test double."""

    def __init__(self, root_folder: OutlookFolder, mounted_file_path: str) -> None:
        """Store fake namespace state."""
        self.Stores = OutlookStores()
        self._root_folder = root_folder
        self._mounted_file_path = mounted_file_path
        self.added_paths: list[str] = []
        self.removed_roots: list[OutlookFolder] = []

    def AddStore(self, file_path: str) -> None:  # noqa: N802
        """Pretend to mount a PST file."""
        self.added_paths.append(file_path)
        self.Stores.append(OutlookStore(self._mounted_file_path, self._root_folder))

    def RemoveStore(self, root_folder: OutlookFolder) -> None:  # noqa: N802
        """Pretend to detach a PST file."""
        self.removed_roots.append(root_folder)
        self.Stores.remove_root(root_folder)


class OutlookApplication:
    """Minimal Outlook application test double."""

    def __init__(self, namespace: OutlookNamespace) -> None:
        """Store the fake namespace."""
        self._namespace = namespace

    def GetNamespace(self, name: str) -> OutlookNamespace:  # noqa: N802
        """Return the fake MAPI namespace."""
        assert name == "MAPI"
        return self._namespace


class Win32Client:
    """Minimal win32com.client module test double."""

    def __init__(self, namespace: OutlookNamespace) -> None:
        """Store the fake namespace."""
        self._namespace = namespace

    def Dispatch(self, name: str) -> OutlookApplication:  # noqa: N802
        """Return the fake Outlook application."""
        assert name == "Outlook.Application"
        return OutlookApplication(self._namespace)


def test_meetings_from_outlook_folder_filters_calendar_appointments() -> None:
    """Test PST extraction keeps only timed appointment items."""
    root = OutlookFolder(
        [
            OutlookAppointment(
                "Imported PST Meeting",
                datetime(2023, 7, 1, 10, 0, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 1, 11, 30, tzinfo=calendar_analyzer.PACIFIC),
            ),
            OutlookAppointment(
                "All Day PST Event",
                datetime(2023, 7, 1, 0, 0, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 2, 0, 0, tzinfo=calendar_analyzer.PACIFIC),
                all_day=True,
            ),
            OutlookAppointment(
                "Free PST Hold",
                datetime(2023, 7, 1, 12, 0, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 1, 13, 0, tzinfo=calendar_analyzer.PACIFIC),
                busy_status=0,
            ),
            object(),
        ]
    )

    meetings = calendar_analyzer._meetings_from_outlook_folder(root)  # noqa: SLF001

    assert [meeting["summary"] for meeting in meetings] == ["Imported PST Meeting"]
    assert meetings[0]["duration_hours"] == 1.5


def test_meetings_from_outlook_folder_skips_non_calendar_folders() -> None:
    """Test PST extraction does not enumerate mail/contact/task folder items."""
    non_calendar_folder = OutlookFolder(
        [],
        default_item_type=0,
    )
    non_calendar_folder.Items = UnreadableOutlookItems()
    calendar_folder = OutlookFolder(
        [
            OutlookAppointment(
                "Calendar Child Meeting",
                datetime(2023, 7, 1, 10, 0, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 1, 11, 0, tzinfo=calendar_analyzer.PACIFIC),
            )
        ]
    )
    root = OutlookFolder([], [non_calendar_folder, calendar_folder], default_item_type=0)

    meetings = calendar_analyzer._meetings_from_outlook_folder(root)  # noqa: SLF001

    assert [meeting["summary"] for meeting in meetings] == ["Calendar Child Meeting"]


def test_import_calendar_meetings_imports_pst_via_outlook_and_detaches_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Test the public PST importer uses Outlook COM and removes its mounted store."""
    pst_path = tmp_path / "calendar.pst"
    pst_path.write_bytes(b"pst")
    root_folder = OutlookFolder(
        [
            OutlookAppointment(
                "Public PST Import",
                datetime(2023, 7, 1, 10, 0, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 1, 11, 0, tzinfo=calendar_analyzer.PACIFIC),
            )
        ]
    )
    namespace = OutlookNamespace(root_folder, str(tmp_path / "outlook-normalized-calendar.pst"))

    def import_module(name: str) -> object:
        if name == "win32com.client":
            return Win32Client(namespace)
        if name == "pywintypes":
            raise ImportError
        return importlib.import_module(name)

    monkeypatch.setattr(calendar_analyzer.sys, "platform", "win32")
    monkeypatch.setattr(calendar_analyzer.importlib, "import_module", import_module)

    meetings = calendar_analyzer.import_calendar_meetings(pst_path)

    assert [meeting["summary"] for meeting in meetings] == ["Public PST Import"]
    assert namespace.added_paths == [str(pst_path)]
    assert namespace.removed_roots == [root_folder]
    assert namespace.Stores.Count == 0
    assert isinstance(root_folder.Items, OutlookItems)
    assert root_folder.Items.IncludeRecurrences is False


def test_import_calendar_meetings_uses_existing_pst_store_without_detaching(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Test the PST importer preserves a store the user already had mounted."""
    pst_path = tmp_path / "calendar.pst"
    pst_path.write_bytes(b"pst")
    root_folder = OutlookFolder(
        [
            OutlookAppointment(
                "Already Mounted PST Import",
                datetime(2023, 7, 1, 10, 0, tzinfo=calendar_analyzer.PACIFIC),
                datetime(2023, 7, 1, 11, 0, tzinfo=calendar_analyzer.PACIFIC),
            )
        ]
    )
    namespace = OutlookNamespace(root_folder, str(tmp_path / "unused-mounted-path.pst"))
    namespace.Stores.append(OutlookStore(str(pst_path.resolve()), root_folder))

    def import_module(name: str) -> object:
        if name == "win32com.client":
            return Win32Client(namespace)
        if name == "pywintypes":
            raise ImportError
        return importlib.import_module(name)

    monkeypatch.setattr(calendar_analyzer.sys, "platform", "win32")
    monkeypatch.setattr(calendar_analyzer.importlib, "import_module", import_module)

    meetings = calendar_analyzer.import_calendar_meetings(pst_path)

    assert [meeting["summary"] for meeting in meetings] == ["Already Mounted PST Import"]
    assert namespace.added_paths == []
    assert namespace.removed_roots == []
    assert namespace.Stores.Count == 1
