# AGENTS.md

Essential guidance for AI assistants working in this repository.

This is the entry point for coding agents. Keep changes small, preserve the
calendar analyzer's user-facing behavior, and validate with the local workflow.

## Core Rules

### Git Operations

- Do not run `git add`, `git commit`, `git push`, `git tag`, or other commands
  that modify version control state unless the user explicitly asks for that
  exact action in the current turn.
- Read-only git commands are fine when needed for context, but prefer
  `git --no-pager ...` for readable output.
- If files are already staged, do not assume the staging was intentional for
  your current task. Ask before changing the index.

### Code Editing

- Use the agent patch editing mechanism for manual edits.
- Do not use `sed`, `awk`, `perl`, shell redirection, or Python scripts to
  modify repository files.
- Keep edits scoped to the requested behavior and existing project style.
- This is a single-file Python CLI with tests; avoid unnecessary abstraction.

### Privacy And Output

- Calendar data is local and can contain private meeting titles, attendees,
  locations, and notes.
- The default CLI intentionally prints the requested meeting-title summary,
  including the top meeting titles. Do not remove or redact that behavior unless
  the user explicitly asks.
- Avoid debug-printing raw calendar records, CSV rows, XML appointments, or
  unreviewed parsed structures. Route intentional user-facing output through
  `generate_summary(...)`.
- If CodeQL flags intentional title output, document the intent narrowly instead
  of changing the product behavior around the scanner.

## Validation Workflow

Use the `justfile` recipes:

```bash
just check
just ci
just security
just test
just fix
```

Common direct checks:

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run ty check calendar_analyzer.py tests --error all
uv run semgrep --error --strict --timeout 30 --config semgrep.yaml .
```

Codex sandbox shells may not include Homebrew on `PATH`. When needed, prefer:

```bash
PATH=/opt/homebrew/bin:$PATH just check
PATH=/opt/homebrew/bin:$PATH uv run pytest
```

## Project Context

- Language: Python 3.11+
- Package/dependency manager: `uv`
- Workflow runner: `just`
- Main module: `calendar_analyzer.py`
- Tests: `tests/test_calendar_analyzer.py`
- Security rules: `semgrep.yaml`, CodeQL workflow
- Supported calendar inputs: Apple `.ics`, `.icbu`, `.sqlitedb`; Outlook `.olm`,
  `.ics`, and `.csv`

## Design Principles

- Preserve the CLI contract documented in `README.md`.
- Prefer explicit, user-facing errors over tracebacks for malformed calendar
  inputs.
- Keep calendar parsing tolerant across export variants, but validate enough to
  avoid treating arbitrary files as calendars.
- Keep security rules repository-specific. Do not duplicate broad CodeQL, Ruff,
  or pip-audit coverage in Semgrep unless there is a clear project invariant.
- Tests should cover user-visible behavior and parsing edge cases, especially
  date handling, export formats, and privacy-sensitive output paths.
