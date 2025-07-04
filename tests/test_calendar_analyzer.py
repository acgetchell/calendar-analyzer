# test_calendar_analyzer.py
import tempfile
import textwrap
import calendar_analyzer

def test_analyze_mock_ics(monkeypatch, capsys):
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

    with tempfile.NamedTemporaryFile(suffix=".ics", mode="w+", delete=False) as tmp:
        tmp.write(ics_content)
        tmp.flush()

        # Step 2: Patch arguments to simulate CLI input
        monkeypatch.setattr("sys.argv", [
            "calendar_analyzer.py",
            "--calendar", tmp.name,
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