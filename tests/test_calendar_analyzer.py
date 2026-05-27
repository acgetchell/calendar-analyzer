"""Tests for calendar_analyzer module."""

import os
import sqlite3
import tempfile
import textwrap
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pytest

import calendar_analyzer


def create_temp_ics_file(content: str, suffix: str = ".ics") -> str:
    """Helper function to create a temporary ICS file with specified content.

    Args:
        content (str): The ICS content to write to the file
        suffix (str): File suffix (default: ".ics")

    Returns:
        str: Path to the created temporary file

    Note:
        The caller is responsible for cleaning up the file using os.unlink()
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w+", delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        return tmp.name


def create_temp_olm_file(calendar_xml: str) -> str:
    """Helper function to create a temporary OLM-like archive with Calendar.xml."""
    with tempfile.NamedTemporaryFile(suffix=".olm", delete=False) as tmp:
        tmp_path = tmp.name
    with zipfile.ZipFile(tmp_path, "w") as archive:
        archive.writestr("Accounts/Calendar.xml", calendar_xml)
    return tmp_path


def create_temp_dummy_file(suffix: str = ".ics") -> str:
    """Helper function to create a temporary dummy file path.

    Args:
        suffix (str): File suffix (default: ".ics")

    Returns:
        str: Path to the created temporary file

    Note:
        The caller is responsible for cleaning up the file using os.unlink()
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as dummy_file:
        return dummy_file.name


def test_analyze_mock_ics(monkeypatch, capsys) -> None:
    """Test analyzing a mock ICS calendar file with sample events."""
    # Step 1: Create a mock ICS calendar file
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:Test Meeting
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:20230702T150000Z
    DURATION:PT2H
    SUMMARY:Project Sync
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_path = create_temp_ics_file(ics_content)

    # Step 2: Patch arguments to simulate CLI input
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

    # Step 3: Run the script
    calendar_analyzer.main()

    # Step 4: Capture and validate output
    out = capsys.readouterr().out
    assert "Test Meeting" in out
    assert "Project Sync" in out
    assert "Total Meetings: 2" in out
    assert "Total Meeting Hours: 3.0" in out


def test_main_excludes_titles_by_regex(monkeypatch, capsys, tmp_path: Path) -> None:
    """Test title exclusion regexes remove matching meetings from stats and output."""
    calendar_path = tmp_path / "calendar.ics"
    calendar_path.write_text(
        textwrap.dedent("""
        BEGIN:VCALENDAR
        VERSION:2.0
        BEGIN:VEVENT
        DTSTART:20230701T100000Z
        DURATION:PT1H
        SUMMARY:SVM Town Hall
        END:VEVENT
        BEGIN:VEVENT
        DTSTART:20230701T120000Z
        DURATION:PT1H
        SUMMARY:All VMTH Meeting
        END:VEVENT
        BEGIN:VEVENT
        DTSTART:20230701T140000Z
        DURATION:PT1H
        SUMMARY:Project Sync
        END:VEVENT
        END:VCALENDAR
        """),
        encoding="utf-8",
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
    calendar_path = tmp_path / "calendar.ics"
    calendar_path.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
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
    # Create a mock ICS file
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:Test Meeting
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_path = create_temp_ics_file(ics_content)

    monkeypatch.setattr(
        "sys.argv",
        ["calendar-analyzer", "--calendar", tmp_path, "--start-date", "2023-06-30", "--end-date", "2023-07-31"],
    )

    # Should not raise SystemExit
    calendar_analyzer.main()

    out = capsys.readouterr().out
    assert "Test Meeting" in out


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
    assert "Apple Calendar: select the calendar, then use File > Export" in out
    assert "Outlook for Mac: export an Outlook archive (.olm)" in out
    assert "Outlook can also be analyzed from exported ICS or CSV calendar files" in out
    assert "just run --calendar" in out


def test_generate_summary_no_meetings() -> None:
    """Test generate_summary with no meetings."""
    meetings: list[calendar_analyzer.Meeting] = []
    stats: calendar_analyzer.MeetingStats = {"total_meetings": 0, "total_hours": 0.0}

    result = calendar_analyzer.generate_summary(meetings, stats)
    assert result == "No meetings found in the specified time period."


def test_file_output_functionality(monkeypatch, capsys) -> None:
    """Test saving analysis to a file."""
    # Create a mock ICS file
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:Test Meeting
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_ics_path = create_temp_ics_file(ics_content)

    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tmp_output:
        output_path = tmp_output.name

    monkeypatch.setattr(
        "sys.argv",
        [
            "calendar-analyzer",
            "--calendar",
            tmp_ics_path,
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
    Path(tmp_ics_path).unlink()
    Path(output_path).unlink()


def test_file_output_error(monkeypatch, capsys) -> None:
    """Test file output error handling."""
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:Test Meeting
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_path = create_temp_ics_file(ics_content)

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
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:Test Meeting
    END:VEVENT
    END:VCALENDAR
    """)
    tmp_ics_path = create_temp_ics_file(ics_content)
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
            tmp_ics_path,
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
    Path(tmp_ics_path).unlink()
    output_path.unlink()


def test_calendar_file_read_error(monkeypatch, capsys) -> None:
    """Test error handling when calendar file cannot be read."""
    monkeypatch.setattr("sys.argv", ["calendar-analyzer", "--calendar", "/nonexistent/file.ics"])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error reading calendar file:" in out


def test_analyze_calendar_with_different_duration_formats() -> None:
    """Test calendar analysis with various duration formats."""
    # Test with DTEND instead of DURATION
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DTEND:20230701T120000Z
    SUMMARY:Meeting with DTEND
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:20230701T140000Z
    SUMMARY:Meeting without duration
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_path = create_temp_ics_file(ics_content)

    _, stats = calendar_analyzer.analyze_calendar(
        Path(tmp_path),
        datetime(2023, 6, 30, tzinfo=calendar_analyzer.PACIFIC),
        datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
    )

    # Should have 2 meetings
    assert stats["total_meetings"] == 2
    # First meeting should have some duration, second defaults to 1 hour
    assert stats["total_hours"] >= 2.0

    # Clean up
    Path(tmp_path).unlink()


def test_generate_summary_with_long_titles() -> None:
    """Test generate_summary with very long meeting titles."""
    # Create meetings with long titles
    long_title = "A" * 150  # 150 character title
    meetings: list[calendar_analyzer.Meeting] = [
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

    stats: calendar_analyzer.MeetingStats = {"total_meetings": 2, "total_hours": 2.0}

    result = calendar_analyzer.generate_summary(meetings, stats, calendar_analyzer.SummaryOptions(num_titles=5))

    # Long title should be truncated
    assert "A" * 100 + "..." in result
    assert "Short title" in result
    assert "Total Meetings: 2" in result
    assert "Average Meetings per Day: 1.0" in result


def test_generate_summary_limits_common_times_and_titles() -> None:
    """Test summary uses requested limits for common times and titles."""
    meetings: list[calendar_analyzer.Meeting] = [
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
    stats: calendar_analyzer.MeetingStats = {"total_meetings": 4, "total_hours": 4.0}

    result = calendar_analyzer.generate_summary(
        meetings,
        stats,
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
    meetings: list[calendar_analyzer.Meeting] = [
        {
            "date": datetime(2023, 7, 5, tzinfo=calendar_analyzer.PACIFIC).date(),
            "time": datetime(2023, 7, 5, 10, 0, tzinfo=calendar_analyzer.PACIFIC).time(),
            "summary": "Middle Meeting",
            "duration_hours": 1.0,
        }
    ]
    stats: calendar_analyzer.MeetingStats = {"total_meetings": 1, "total_hours": 1.0}

    result = calendar_analyzer.generate_summary(
        meetings,
        stats,
        calendar_analyzer.SummaryOptions(
            period_start=datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            period_end=datetime(2023, 7, 11, tzinfo=calendar_analyzer.PACIFIC),
        ),
    )

    assert "- From: July 01, 2023" in result
    assert "- To:   July 11, 2023" in result
    assert "- Span: 10 days" in result
    assert "- Average Meetings per Day: 0.1" in result


def test_analyze_calendar_date_filtering() -> None:
    """Test that date filtering works correctly."""
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230615T100000Z
    DURATION:PT1H
    SUMMARY:Before Range
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:In Range
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:20230715T100000Z
    DURATION:PT1H
    SUMMARY:After Range
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_path = create_temp_ics_file(ics_content)

    meetings, stats = calendar_analyzer.analyze_calendar(
        Path(tmp_path),
        datetime(2023, 6, 30, tzinfo=calendar_analyzer.PACIFIC),
        datetime(2023, 7, 5, tzinfo=calendar_analyzer.PACIFIC),
    )

    # Should only have the meeting in range
    assert stats["total_meetings"] == 1
    assert meetings[0]["summary"] == "In Range"

    # Clean up
    Path(tmp_path).unlink()


def test_analyze_calendar_skips_ics_all_day_events(tmp_path: Path) -> None:
    """Test ICS all-day-like events are excluded as calendar blocks."""
    calendar_path = tmp_path / "calendar.ics"
    calendar_path.write_text(
        textwrap.dedent("""
        BEGIN:VCALENDAR
        VERSION:2.0
        BEGIN:VEVENT
        DTSTART;VALUE=DATE:20230701
        DTEND;VALUE=DATE:20230702
        SUMMARY:ICS All Day Event
        END:VEVENT
        BEGIN:VEVENT
        DTSTART:20230701T160000Z
        DTEND:20230701T170000Z
        TRANSP:TRANSPARENT
        SUMMARY:Free Calendar Hold
        END:VEVENT
        BEGIN:VEVENT
        DTSTART:20230702T000000
        DURATION:PT1H
        SUMMARY:Midnight Export Block
        END:VEVENT
        BEGIN:VEVENT
        DTSTART:20230703T090000
        DURATION:PT8H
        SUMMARY:Workday Block
        END:VEVENT
        BEGIN:VEVENT
        DTSTART:20230701T170000Z
        DTEND:20230701T180000Z
        SUMMARY:ICS Timed Meeting
        END:VEVENT
        END:VCALENDAR
        """),
        encoding="utf-8",
    )

    meetings, stats = calendar_analyzer.analyze_calendar(
        calendar_path,
        datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
        datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
    )

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["ICS Timed Meeting"]


def test_get_calendar_path_with_specified_file(capsys) -> None:
    """Test get_calendar_path when a specific file is provided."""
    # Create a temporary file
    tmp_path = create_temp_ics_file("test content")

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
    nonexistent_path = create_temp_dummy_file("_nonexistent.ics")
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
        old_calendar = documents_dir / "old_calendar.ics"
        new_calendar = documents_dir / "new_calendar.ics"

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
    """Test auto-discovery ignores generic CSV files unless explicitly requested."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        home_path = Path(tmp_dir)
        mock_home.return_value = home_path
        documents_dir = home_path / "Documents"
        documents_dir.mkdir()
        (documents_dir / "not-a-calendar.csv").write_text("Amount,Description\n1.00,Coffee\n")

        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.get_calendar_path()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "✗ No calendar files found" in out
        assert "not-a-calendar.csv" not in out


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
        ics_file = documents_dir / "calendar.ics"
        icbu_file = library_dir / "backup.icbu"
        sqlite_file = library_dir / "calendar.sqlitedb"
        olm_file = documents_dir / "calendar.olm"

        ics_file.write_text("ics content")
        icbu_file.write_text("icbu content")
        sqlite_file.write_text("sqlite content")
        olm_file.write_text("olm content")

        result = calendar_analyzer.get_calendar_path()

        # Should find one of the files (the most recent one)
        assert result.exists()
        assert result.suffix in [".ics", ".icbu", ".sqlitedb", ".olm"]

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
            calendar_file = documents_dir / f"calendar_{i}.ics"
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
        nested_calendar = nested_dir / "nested_calendar.ics"
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

        # Create a fake SQLite database file inside ICBU
        sqlite_path = icbu_path / "Calendar.sqlitedb"
        sqlite_path.write_text("fake sqlite content")

        # Mock the analyze_sqlite_calendar function
        with patch("calendar_analyzer.analyze_sqlite_calendar") as mock_sqlite:
            mock_sqlite.return_value = ([], {"total_meetings": 0, "total_hours": 0})

            result = calendar_analyzer.analyze_calendar(icbu_path)

            # Should have called analyze_sqlite_calendar
            mock_sqlite.assert_called_once_with(sqlite_path, None, None, calendar_analyzer.DEFAULT_DAYS_BACK)
            assert result == ([], {"total_meetings": 0, "total_hours": 0})

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

        meetings, stats = calendar_analyzer.analyze_calendar(
            sqlite_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
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

        meetings, stats = calendar_analyzer.analyze_calendar(
            sqlite_path,
            end_date=datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            days_back=1,
        )

        assert meetings == []
        assert stats == {"total_meetings": 0, "total_hours": 0.0}


def test_analyze_sqlite_calendar_skips_all_day_rows() -> None:
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

        meetings, stats = calendar_analyzer.analyze_sqlite_calendar(
            sqlite_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
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
        meetings, stats = calendar_analyzer.analyze_calendar(
            Path(tmp_path),
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.5}
    assert meetings[0]["summary"] == "Outlook Mac Meeting"
    assert meetings[0]["time"].hour == 10


def test_analyze_olm_calendar_defaults_missing_end_and_summary() -> None:
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
        meetings, stats = calendar_analyzer.analyze_olm_calendar(
            Path(tmp_path),
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert meetings[0]["summary"] == "No Title"


def test_analyze_olm_calendar_filters_requested_range() -> None:
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
        meetings, stats = calendar_analyzer.analyze_olm_calendar(
            Path(tmp_path),
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 4, tzinfo=calendar_analyzer.PACIFIC),
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["In Range"]


def test_analyze_olm_calendar_skips_all_day_and_date_only_appointments() -> None:
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
        meetings, stats = calendar_analyzer.analyze_olm_calendar(
            Path(tmp_path),
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )
    finally:
        Path(tmp_path).unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["Split Timed Meeting"]
    assert meetings[0]["time"].hour == 10


def test_analyze_olm_calendar_without_calendar_xml(capsys) -> None:
    """Test OLM calendar analysis reports archives without Calendar.xml."""
    with tempfile.NamedTemporaryFile(suffix=".olm", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    with zipfile.ZipFile(tmp_path, "w") as archive:
        archive.writestr("Accounts/Mail.xml", "<emails />")

    try:
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_olm_calendar(Path(tmp_path))
    finally:
        tmp_path.unlink()

    assert exc_info.value.code == 1
    assert "Error parsing OLM calendar: No Calendar.xml entries found in OLM archive." in capsys.readouterr().out


def test_analyze_olm_calendar_bad_archive(capsys) -> None:
    """Test OLM calendar analysis reports unreadable OLM archives."""
    with tempfile.NamedTemporaryFile(suffix=".olm", delete=False) as tmp:
        tmp.write(b"not a zip archive")
        tmp_path = Path(tmp.name)

    try:
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_olm_calendar(Path(tmp_path))
    finally:
        tmp_path.unlink()

    assert exc_info.value.code == 1
    assert "Error reading OLM calendar:" in capsys.readouterr().out


def test_analyze_olm_calendar_bad_xml(capsys) -> None:
    """Test OLM calendar analysis reports malformed Calendar.xml content."""
    with tempfile.NamedTemporaryFile(suffix=".olm", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    with zipfile.ZipFile(tmp_path, "w") as archive:
        archive.writestr("Accounts/Calendar.xml", "<appointments><appointment></appointments>")

    try:
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_olm_calendar(Path(tmp_path))
    finally:
        tmp_path.unlink()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing OLM calendar: Accounts/Calendar.xml is not valid XML:" in out


def test_analyze_olm_calendar_errors_when_no_start_dates_parse(capsys) -> None:
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
            calendar_analyzer.analyze_olm_calendar(Path(tmp_path))
    finally:
        Path(tmp_path).unlink()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing OLM calendar: Could not parse any OLM appointment start dates." in out
    assert "not-a-date" in out


def test_analyze_olm_calendar_errors_when_split_start_time_invalid(capsys) -> None:
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
            calendar_analyzer.analyze_olm_calendar(Path(tmp_path))
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
        meetings, stats = calendar_analyzer.analyze_calendar(
            tmp_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )
    finally:
        tmp_path.unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.5}
    assert meetings[0]["summary"] == "CSV Meeting"
    assert meetings[0]["time"].hour == 10


def test_analyze_outlook_csv_calendar_read_error(capsys, tmp_path: Path) -> None:
    """Test Outlook CSV analysis reports unreadable files."""
    missing_path = tmp_path / "missing.csv"

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_outlook_csv_calendar(missing_path)

    assert exc_info.value.code == 1
    assert "Error reading Outlook CSV calendar:" in capsys.readouterr().out


def test_analyze_outlook_csv_calendar_requires_header(capsys, tmp_path: Path) -> None:
    """Test Outlook CSV analysis reports an empty CSV file."""
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_outlook_csv_calendar(csv_path)

    assert exc_info.value.code == 1
    assert "Error parsing Outlook CSV calendar: CSV header row is missing." in capsys.readouterr().out


def test_analyze_outlook_csv_calendar_requires_start_columns(capsys, tmp_path: Path) -> None:
    """Test Outlook CSV analysis rejects non-calendar CSV files."""
    csv_path = tmp_path / "not-calendar.csv"
    csv_path.write_text("Amount,Description\n1.00,Coffee\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_outlook_csv_calendar(csv_path)

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error parsing Outlook CSV calendar:" in out
    assert "CSV must include Outlook start columns" in out


def test_analyze_outlook_csv_calendar_filters_requested_range(tmp_path: Path) -> None:
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

    meetings, stats = calendar_analyzer.analyze_outlook_csv_calendar(
        csv_path,
        datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
        datetime(2023, 7, 4, tzinfo=calendar_analyzer.PACIFIC),
    )

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["In Range"]


def test_analyze_outlook_csv_calendar_skips_date_only_rows() -> None:
    """Test Outlook CSV all-day-like rows are excluded."""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", newline="", delete=False) as tmp:
        tmp.write("Subject,Start Date,Start Time,End Date,End Time,Show Time As\n")
        tmp.write("CSV Timed Meeting,07/01/2023,10:00 AM,07/01/2023,11:00 AM,Busy\n")
        tmp.write("CSV Date Only Event,07/01/2023,,07/01/2023,,Busy\n")
        tmp.write("CSV Free Hold,07/01/2023,12:00 PM,07/01/2023,1:00 PM,Free\n")
        tmp.write("CSV Midnight Export Block,07/02/2023,12:00 AM,07/02/2023,1:00 AM,Busy\n")
        tmp.write("CSV Workday Block,07/03/2023,9:00 AM,07/03/2023,5:00 PM,Busy\n")
        tmp_path = Path(tmp.name)

    try:
        meetings, stats = calendar_analyzer.analyze_outlook_csv_calendar(
            tmp_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )
    finally:
        tmp_path.unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["CSV Timed Meeting"]


def test_analyze_outlook_csv_calendar_skips_combined_date_only_start() -> None:
    """Test combined Outlook Start columns need a time component."""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", encoding="utf-8", newline="", delete=False) as tmp:
        tmp.write("Subject,Start,End\n")
        tmp.write("CSV Combined Timed,2023-07-01 10:00:00,2023-07-01 11:00:00\n")
        tmp.write("CSV Combined Date Only,2023-07-01,2023-07-01\n")
        tmp_path = Path(tmp.name)

    try:
        meetings, stats = calendar_analyzer.analyze_outlook_csv_calendar(
            tmp_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )
    finally:
        tmp_path.unlink()

    assert stats == {"total_meetings": 1, "total_hours": 1.0}
    assert [meeting["summary"] for meeting in meetings] == ["CSV Combined Timed"]


def test_analyze_sqlite_calendar_defaults_missing_summary() -> None:
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

        meetings, stats = calendar_analyzer.analyze_sqlite_calendar(
            sqlite_path,
            datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )

        assert stats == {"total_meetings": 1, "total_hours": 1.0}
        assert meetings[0]["summary"] == "No Title"


def test_analyze_sqlite_calendar_read_error(capsys) -> None:
    """Test SQLite analysis reports database read errors."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sqlite_path = Path(tmp_dir) / "calendar.sqlitedb"
        sqlite_path.write_text("not a sqlite database")

        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.analyze_sqlite_calendar(sqlite_path)

        assert exc_info.value.code == 1
        assert "Error reading SQLite calendar:" in capsys.readouterr().out


def test_analyze_calendar_icbu_with_ics_fallback(capsys) -> None:
    """Test ICBU file handling with ICS fallback when no SQLite."""
    # Create ICS content
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:ICBU Test Meeting
    END:VEVENT
    END:VCALENDAR
    """)

    with tempfile.TemporaryDirectory() as tmp_dir:
        icbu_path = Path(tmp_dir) / "backup.icbu"
        icbu_path.mkdir()

        # Create ICS file inside ICBU (no SQLite)
        ics_path = icbu_path / "calendar.ics"
        ics_path.write_text(ics_content)

        meetings, stats = calendar_analyzer.analyze_calendar(
            icbu_path,
            datetime(2023, 6, 30, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )

        assert stats["total_meetings"] == 1
        assert meetings[0]["summary"] == "ICBU Test Meeting"

        out = capsys.readouterr().out
        assert f"Found ICS file in ICBU backup: {ics_path}" in out


def test_analyze_calendar_icbu_uses_sorted_ics_fallback(capsys) -> None:
    """Test ICBU fallback chooses a deterministic ICS file."""
    first_ics = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:First ICS Meeting
    END:VEVENT
    END:VCALENDAR
    """)
    second_ics = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:Second ICS Meeting
    END:VEVENT
    END:VCALENDAR
    """)

    with tempfile.TemporaryDirectory() as tmp_dir:
        icbu_path = Path(tmp_dir) / "backup.icbu"
        icbu_path.mkdir()
        selected_ics_path = icbu_path / "a-calendar.ics"
        selected_ics_path.write_text(first_ics)
        (icbu_path / "z-calendar.ics").write_text(second_ics)

        meetings, stats = calendar_analyzer.analyze_calendar(
            icbu_path,
            datetime(2023, 6, 30, tzinfo=calendar_analyzer.PACIFIC),
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
        )

        assert stats["total_meetings"] == 1
        assert meetings[0]["summary"] == "First ICS Meeting"
        out = capsys.readouterr().out
        assert f"Found ICS file in ICBU backup: {selected_ics_path}" in out


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
        assert f"Error: Could not find calendar data (SQLite or ICS) in {icbu_path}" in out
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


def test_analyze_calendar_with_malformed_ics(capsys) -> None:
    """Test analyze_calendar with malformed ICS content."""
    malformed_ics = "This is not valid ICS content"

    tmp_path = create_temp_ics_file(malformed_ics)

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.analyze_calendar(Path(tmp_path))

    assert exc_info.value.code == 1
    assert "Error parsing calendar file:" in capsys.readouterr().out

    # Clean up
    Path(tmp_path).unlink()


def test_analyze_calendar_default_date_range() -> None:
    """Test analyze_calendar with default date ranges (no start/end specified)."""
    # Use a recent date that would be within the default 365-day range
    recent_date = datetime.now(UTC) - timedelta(days=30)  # 30 days ago
    recent_date_str = recent_date.strftime("%Y%m%dT%H%M%SZ")

    ics_content = textwrap.dedent(f"""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:{recent_date_str}
    DURATION:PT1H
    SUMMARY:Recent Meeting
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_path = create_temp_ics_file(ics_content)

    # Test with default date range (past 365 days)
    meetings, stats = calendar_analyzer.analyze_calendar(Path(tmp_path))

    # Should process the calendar and find the recent meeting
    assert isinstance(meetings, list)
    assert isinstance(stats, dict)
    assert stats["total_meetings"] == 1
    assert stats["total_hours"] == 1.0
    assert meetings[0]["summary"] == "Recent Meeting"

    # Clean up
    Path(tmp_path).unlink()


def test_analyze_calendar_with_non_datetime_events() -> None:
    """Test analyze_calendar with events that have non-datetime start times."""
    # ICS with all-day event (DATE instead of DATETIME)
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART;VALUE=DATE:20230701
    SUMMARY:All Day Event
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT1H
    SUMMARY:Timed Event
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_path = create_temp_ics_file(ics_content)

    meetings, stats = calendar_analyzer.analyze_calendar(
        Path(tmp_path),
        datetime(2023, 6, 30, tzinfo=calendar_analyzer.PACIFIC),
        datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
    )

    # Should only process the datetime event, not the all-day event
    assert stats["total_meetings"] == 1
    assert meetings[0]["summary"] == "Timed Event"

    # Clean up
    Path(tmp_path).unlink()


def test_analyze_calendar_duration_parsing_edge_cases() -> None:
    """Test various duration parsing edge cases."""
    # Test various duration formats that might cause issues
    ics_content = textwrap.dedent("""
    BEGIN:VCALENDAR
    VERSION:2.0
    BEGIN:VEVENT
    DTSTART:20230701T100000Z
    DURATION:PT30M
    SUMMARY:30 Minute Meeting
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:20230701T120000Z
    DURATION:P1D
    SUMMARY:All Day Event with Duration
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:20230701T140000Z
    DURATION:INVALID_DURATION
    SUMMARY:Invalid Duration Meeting
    END:VEVENT
    END:VCALENDAR
    """)

    tmp_path = create_temp_ics_file(ics_content)

    meetings, stats = calendar_analyzer.analyze_calendar(
        Path(tmp_path),
        datetime(2023, 6, 30, tzinfo=calendar_analyzer.PACIFIC),
        datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
    )

    # Should process timed meetings, with fallback durations where needed
    assert stats["total_meetings"] == 2

    # Find each meeting and check duration handling
    meeting_summaries = [m["summary"] for m in meetings]
    assert "30 Minute Meeting" in meeting_summaries
    assert "All Day Event with Duration" not in meeting_summaries
    assert "Invalid Duration Meeting" in meeting_summaries

    # Clean up
    Path(tmp_path).unlink()


class CalendarProperty:
    """Minimal calendar property wrapper for fake event values."""

    def __init__(self, value: object) -> None:
        """Store a value on the same attribute used by icalendar properties."""
        self.dt = value


class CalendarEvent:
    """Minimal event object that supports the calendar analyzer event API."""

    def __init__(self, start: datetime, summary: str, duration: object) -> None:
        """Create a fake event with start, summary, and duration fields."""
        self.values = {
            "dtstart": CalendarProperty(start),
            "duration": duration,
            "summary": summary,
        }

    def get(self, key: str, default: object = None) -> object:
        """Return the requested fake event field."""
        return self.values.get(key, default)


class CalendarDuration:
    """Minimal duration object whose string representation matches ICS syntax."""

    def __init__(self, value: str) -> None:
        """Store the duration text."""
        self.value = value

    def __str__(self) -> str:
        """Return the ICS-style duration text."""
        return self.value


class CalendarWithEvents:
    """Minimal calendar object that returns fake VEVENT entries."""

    def __init__(self, events: list[CalendarEvent]) -> None:
        """Store fake events for later traversal."""
        self.events = events

    def walk(self, component: str) -> list[CalendarEvent]:
        """Return fake VEVENT entries."""
        assert component == "VEVENT"
        return self.events


def test_analyze_calendar_duration_timedelta_and_string_branches(monkeypatch) -> None:
    """Test public calendar analysis for timedelta and string duration inputs."""
    start = datetime(2023, 7, 1, 17, 0, tzinfo=UTC)
    calendar = CalendarWithEvents(
        [
            CalendarEvent(start, "Timedelta Duration", timedelta(hours=2)),
            CalendarEvent(start, "String Hour Duration", CalendarDuration("PT3H")),
            CalendarEvent(start, "Fallback String Duration", CalendarDuration("PT30M")),
            CalendarEvent(start, "Malformed Hour Duration", CalendarDuration("PTXH")),
        ]
    )
    monkeypatch.setattr("calendar_analyzer._read_ics_calendar", lambda _: calendar)

    meetings, stats = calendar_analyzer.analyze_calendar(
        Path("fake.ics"),
        datetime(2023, 7, 1, tzinfo=calendar_analyzer.PACIFIC),
        datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC),
    )

    durations_by_title = {meeting["summary"]: meeting["duration_hours"] for meeting in meetings}
    assert durations_by_title == {
        "Timedelta Duration": 2.0,
        "String Hour Duration": 3.0,
        "Fallback String Duration": calendar_analyzer.DEFAULT_DURATION_HOURS,
        "Malformed Hour Duration": calendar_analyzer.DEFAULT_DURATION_HOURS,
    }
    assert stats == {"total_meetings": 4, "total_hours": 7.0}
