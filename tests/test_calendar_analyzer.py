"""Tests for calendar_analyzer module."""

# Standard library imports
import os
import tempfile
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Third-party imports
import pytest
from dateutil import tz

# Local imports
import calendar_analyzer


def create_temp_ics_file(content, suffix=".ics"):
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


def create_temp_dummy_file(suffix=".ics"):
    """Helper function to create a temporary dummy file path.

    Args:
        suffix (str): File suffix (default: ".ics")

    Returns:
        str: Path to the created temporary file

    Note:
        The caller is responsible for cleaning up the file using os.unlink()
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as dummy_file:
        dummy_path = dummy_file.name
    return dummy_path


def test_analyze_mock_ics(monkeypatch, capsys):
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
    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", tmp_path,
        "--start-date", "2023-06-30",
        "--end-date", "2023-07-03",
        "--titles", "10"
    ])

    # Step 3: Run the script
    calendar_analyzer.main()

    # Step 4: Capture and validate output
    out = capsys.readouterr().out
    assert "Test Meeting" in out
    assert "Project Sync" in out
    assert "Total Meetings: 2" in out
    assert "Total Meeting Hours: 3.0" in out


def test_invalid_start_date_format(monkeypatch, capsys):
    """Test that invalid start date format causes system exit."""
    # Create a temporary dummy file path (secure alternative to mktemp)
    dummy_path = create_temp_dummy_file()

    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", dummy_path,
        "--start-date", "invalid-date"
    ])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error: Start date must be in YYYY-MM-DD format" in out


def test_invalid_end_date_format(monkeypatch, capsys):
    """Test that invalid end date format causes system exit."""
    # Create a temporary dummy file path (secure alternative to mktemp)
    dummy_path = create_temp_dummy_file()

    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", dummy_path,
        "--end-date", "2023/01/01"
    ])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error: End date must be in YYYY-MM-DD format" in out


def test_end_date_before_start_date(monkeypatch, capsys):
    """Test that end date before start date causes system exit."""
    # Create a temporary dummy file path (secure alternative to mktemp)
    dummy_path = create_temp_dummy_file()

    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", dummy_path,
        "--start-date", "2023-07-01",
        "--end-date", "2023-06-30"
    ])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error: End date cannot be before start date" in out
    assert "Start date: 2023-07-01" in out
    assert "End date: 2023-06-30" in out


def test_valid_date_formats(monkeypatch, capsys):
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

    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", tmp_path,
        "--start-date", "2023-06-30",
        "--end-date", "2023-07-31"
    ])

    # Should not raise SystemExit
    calendar_analyzer.main()

    out = capsys.readouterr().out
    assert "Test Meeting" in out


def test_edge_case_dates(monkeypatch, capsys):
    """Test edge case date formats."""
    # Test leap year date
    # Create a temporary dummy file path that doesn't exist
    dummy_path = create_temp_dummy_file()
    # Remove the file to make it nonexistent (for this test)
    os.unlink(dummy_path)

    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", dummy_path,
        "--start-date", "2024-02-29"  # Valid leap year date
    ])

    with pytest.raises(SystemExit):  # Will fail because dummy file doesn't exist
        calendar_analyzer.main()

    # Test invalid leap year date
    dummy_path2 = create_temp_dummy_file()
    # Remove this file too since we want to test date validation, not file reading
    os.unlink(dummy_path2)

    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", dummy_path2,
        "--start-date", "2023-02-29"  # Invalid - 2023 is not a leap year
    ])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error: Start date must be in YYYY-MM-DD format" in out


def test_convert_to_pacific():
    """Test timezone conversion function."""
    # Test UTC to Pacific conversion
    utc_time = datetime(2023, 7, 1, 17, 0, 0, tzinfo=tz.UTC)
    pacific_time = calendar_analyzer.convert_to_pacific(utc_time)

    # During PDT (July), UTC-7
    assert pacific_time.hour == 10  # 17:00 UTC = 10:00 PDT

    # Test naive datetime (assumed UTC)
    naive_time = datetime(2023, 7, 1, 17, 0, 0)
    pacific_time = calendar_analyzer.convert_to_pacific(naive_time)
    assert pacific_time.hour == 10


def test_print_calendar_export_instructions(capsys):
    """Test calendar export instructions function."""
    calendar_analyzer.print_calendar_export_instructions()

    out = capsys.readouterr().out
    assert "Please export your calendar from the Calendar app:" in out
    assert "Open the Calendar app" in out
    assert "File > Export" in out
    assert "python calendar_analyzer.py --calendar" in out


def test_generate_summary_no_meetings():
    """Test generate_summary with no meetings."""
    meetings = []
    stats = {'total_meetings': 0, 'total_hours': 0}

    result = calendar_analyzer.generate_summary(meetings, stats)
    assert result == "No meetings found in the specified time period."


def test_file_output_functionality(monkeypatch, capsys):
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

    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", tmp_ics_path,
        "--start-date", "2023-06-30",
        "--end-date", "2023-07-03",
        "--output", output_path
    ])

    calendar_analyzer.main()

    # Check that file was created and contains expected content
    with open(output_path, 'r', encoding='utf-8') as f:
        content = f.read()
        assert "Test Meeting" in content
        assert "Calendar Analysis Summary" in content

    out = capsys.readouterr().out
    assert f"Analysis saved to: {output_path}" in out

    # Clean up
    os.unlink(tmp_ics_path)
    os.unlink(output_path)


def test_file_output_error(monkeypatch, capsys):
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

    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", tmp_path,
        "--start-date", "2023-06-30",
        "--end-date", "2023-07-03",
        "--output", "/invalid/path/output.txt"  # Invalid path
    ])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error saving to file:" in out

    # Clean up
    os.unlink(tmp_path)


def test_calendar_file_read_error(monkeypatch, capsys):
    """Test error handling when calendar file cannot be read."""
    monkeypatch.setattr("sys.argv", [
        "calendar_analyzer.py",
        "--calendar", "/nonexistent/file.ics"
    ])

    with pytest.raises(SystemExit) as exc_info:
        calendar_analyzer.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Error reading calendar file:" in out


def test_analyze_calendar_with_different_duration_formats():
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
        datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC)
    )

    # Should have 2 meetings
    assert stats['total_meetings'] == 2
    # First meeting should have some duration, second defaults to 1 hour
    assert stats['total_hours'] >= 2.0

    # Clean up
    os.unlink(tmp_path)


def test_generate_summary_with_long_titles():
    """Test generate_summary with very long meeting titles."""
    # Create meetings with long titles
    long_title = "A" * 150  # 150 character title
    meetings = [
        {
            'date': datetime(2023, 7, 1).date(),
            'time': datetime(2023, 7, 1, 10, 0).time(),
            'summary': long_title,
            'duration_hours': 1.0
        },
        {
            'date': datetime(2023, 7, 1).date(),
            'time': datetime(2023, 7, 1, 14, 0).time(),
            'summary': 'Short title',
            'duration_hours': 1.0
        }
    ]

    stats = {'total_meetings': 2, 'total_hours': 2.0}

    result = calendar_analyzer.generate_summary(meetings, stats, 5)

    # Long title should be truncated
    assert "A" * 100 + "..." in result
    assert "Short title" in result
    assert "Total Meetings: 2" in result


def test_analyze_calendar_date_filtering():
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
        datetime(2023, 7, 5, tzinfo=calendar_analyzer.PACIFIC)
    )

    # Should only have the meeting in range
    assert stats['total_meetings'] == 1
    assert meetings[0]['summary'] == 'In Range'

    # Clean up
    os.unlink(tmp_path)


def test_get_calendar_path_with_specified_file(capsys):
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
        os.unlink(tmp_path)


def test_get_calendar_path_with_directory(capsys):
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


def test_get_calendar_path_nonexistent_file(capsys):
    """Test get_calendar_path with a nonexistent file."""
    # Use a more secure temporary path that doesn't exist
    nonexistent_path = create_temp_dummy_file("_nonexistent.ics")
    # Remove the file to make it nonexistent but keep the secure path
    os.unlink(nonexistent_path)

    result = calendar_analyzer.get_calendar_path(nonexistent_path)

    assert result == Path(nonexistent_path).resolve()

    out = capsys.readouterr().out
    assert f"Looking for calendar at: {Path(nonexistent_path).resolve()}" in out
    assert "Path exists: False" in out


def test_get_calendar_path_oserror(capsys):
    """Test get_calendar_path when OSError occurs."""
    with patch('pathlib.Path.resolve', side_effect=OSError("Permission denied")):
        with pytest.raises(SystemExit) as exc_info:
            calendar_analyzer.get_calendar_path("/some/path")

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Error processing path: Permission denied" in out


@patch('pathlib.Path.home')
def test_get_calendar_path_auto_discovery_with_files(mock_home, capsys):
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
        old_time = os.path.getmtime(new_calendar) - 3600  # 1 hour ago
        os.utime(old_calendar, (old_time, old_time))

        result = calendar_analyzer.get_calendar_path()

        # Should return the newer file
        assert result == new_calendar

        out = capsys.readouterr().out
        assert "Searching for calendar files in:" in out
        assert "✓ Directory exists" in out
        assert "✓ Found 2 calendar files" in out
        assert f"Selected most recent calendar file: {new_calendar}" in out


@patch('pathlib.Path.home')
def test_get_calendar_path_auto_discovery_no_files(mock_home, capsys):
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
        assert "Please export your calendar from the Calendar app:" in out


@patch('pathlib.Path.home')
def test_get_calendar_path_auto_discovery_nonexistent_dirs(mock_home, capsys):
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


@patch('pathlib.Path.home')
def test_get_calendar_path_auto_discovery_multiple_file_types(mock_home, capsys):
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

        ics_file.write_text("ics content")
        icbu_file.write_text("icbu content")
        sqlite_file.write_text("sqlite content")

        result = calendar_analyzer.get_calendar_path()

        # Should find one of the files (the most recent one)
        assert result.exists()
        assert result.suffix in ['.ics', '.icbu', '.sqlitedb']

        out = capsys.readouterr().out
        # The function prints found files per directory, not total
        assert "✓ Found 2 calendar files" in out  # Library/Calendars
        assert "✓ Found 1 calendar files" in out  # Documents


@patch('pathlib.Path.home')
def test_get_calendar_path_auto_discovery_many_files(mock_home, capsys):
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


@patch('pathlib.Path.home')
def test_get_calendar_path_auto_discovery_subdirectories(mock_home, capsys):
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


def test_analyze_calendar_icbu_with_sqlite(capsys):
    """Test ICBU file handling with SQLite database inside."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        icbu_path = Path(tmp_dir) / "backup.icbu"
        icbu_path.mkdir()

        # Create a fake SQLite database file inside ICBU
        sqlite_path = icbu_path / "Calendar.sqlitedb"
        sqlite_path.write_text("fake sqlite content")

        # Mock the analyze_sqlite_calendar function
        with patch('calendar_analyzer.analyze_sqlite_calendar') as mock_sqlite:
            mock_sqlite.return_value = (
                [], {'total_meetings': 0, 'total_hours': 0})

            result = calendar_analyzer.analyze_calendar(icbu_path)

            # Should have called analyze_sqlite_calendar
            mock_sqlite.assert_called_once_with(sqlite_path, None, None)
            assert result == ([], {'total_meetings': 0, 'total_hours': 0})

            out = capsys.readouterr().out
            assert f"Found SQLite database in ICBU backup: {sqlite_path}" in out


def test_analyze_calendar_icbu_with_ics_fallback(capsys):
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
            datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC)
        )

        assert stats['total_meetings'] == 1
        assert meetings[0]['summary'] == 'ICBU Test Meeting'

        out = capsys.readouterr().out
        assert f"Found ICS file in ICBU backup: {ics_path}" in out


def test_analyze_calendar_icbu_no_calendar_data(capsys):
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


def test_analyze_calendar_icbu_directory_listing_error(capsys):
    """Test ICBU directory listing error handling."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        icbu_path = Path(tmp_dir) / "backup.icbu"
        icbu_path.mkdir()

        # Mock iterdir to raise OSError
        with patch.object(Path, 'iterdir', side_effect=OSError("Permission denied")):
            with pytest.raises(SystemExit) as exc_info:
                calendar_analyzer.analyze_calendar(icbu_path)

            assert exc_info.value.code == 1
            out = capsys.readouterr().out
            assert "Error listing directory contents: Permission denied" in out


def test_analyze_calendar_with_malformed_ics():
    """Test analyze_calendar with malformed ICS content."""
    malformed_ics = "This is not valid ICS content"

    tmp_path = create_temp_ics_file(malformed_ics)

    # The icalendar library will raise a ValueError, which gets caught as an exception
    # but not specifically OSError, so it might not trigger our exception handler
    # Let's test that it raises some kind of exception
    with pytest.raises((SystemExit, ValueError)):
        calendar_analyzer.analyze_calendar(Path(tmp_path))

    # Clean up
    os.unlink(tmp_path)


def test_analyze_calendar_default_date_range():
    """Test analyze_calendar with default date ranges (no start/end specified)."""
    # Use a recent date that would be within the default 365-day range
    recent_date = datetime.now() - timedelta(days=30)  # 30 days ago
    recent_date_str = recent_date.strftime('%Y%m%dT%H%M%SZ')

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
    assert stats['total_meetings'] == 1
    assert stats['total_hours'] == 1.0
    assert meetings[0]['summary'] == 'Recent Meeting'

    # Clean up
    os.unlink(tmp_path)


def test_analyze_calendar_with_non_datetime_events():
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
        datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC)
    )

    # Should only process the datetime event, not the all-day event
    assert stats['total_meetings'] == 1
    assert meetings[0]['summary'] == 'Timed Event'

    # Clean up
    os.unlink(tmp_path)


def test_analyze_calendar_duration_parsing_edge_cases():
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
        datetime(2023, 7, 2, tzinfo=calendar_analyzer.PACIFIC)
    )

    # Should process all events, with fallback durations where needed
    assert stats['total_meetings'] == 3

    # Find each meeting and check duration handling
    meeting_summaries = [m['summary'] for m in meetings]
    assert '30 Minute Meeting' in meeting_summaries
    assert 'All Day Event with Duration' in meeting_summaries
    assert 'Invalid Duration Meeting' in meeting_summaries

    # Clean up
    os.unlink(tmp_path)
