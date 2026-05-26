# Security Policy

## Supported Versions

Use the latest released version from the default branch. Security fixes are not backported to older versions unless noted in a release.

## Reporting a Vulnerability

Please report vulnerabilities privately using GitHub private vulnerability reporting:

<https://github.com/acgetchell/calendar-analyzer/security/advisories/new>

Do not open a public issue for suspected vulnerabilities.

Include:

- Affected version or commit.
- Steps to reproduce.
- Expected and observed behavior.
- Any relevant calendar input shape, with sensitive data removed.

## Security Checks

This project uses GitHub CodeQL, Dependabot security updates, secret scanning with push protection, Ruff security rules, `pip-audit`, and repository-owned Semgrep rules.
