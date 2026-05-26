# shellcheck disable=SC2148
# Calendar Analyzer development workflow.

set shell := ["bash", "-euo", "pipefail", "-c"]

_ensure-powershell-tools:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v pwsh >/dev/null || { echo "'pwsh' not found. Install PowerShell: https://learn.microsoft.com/powershell/"; exit 1; }
    pwsh -NoProfile -Command 'if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) { throw "PSScriptAnalyzer is not installed. Run: Install-Module PSScriptAnalyzer -Scope CurrentUser -Force" }'

_ensure-shell-tools:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v shellcheck >/dev/null || { echo "'shellcheck' not found. Install with: brew install shellcheck (macOS) or winget install koalaman.shellcheck (Windows)."; exit 1; }
    command -v shfmt >/dev/null || { echo "'shfmt' not found. Install with: brew install shfmt (macOS) or winget install mvdan.shfmt (Windows)."; exit 1; }

_ensure-taplo:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v taplo >/dev/null || { echo "'taplo' not found. Install with: brew install taplo"; exit 1; }

_ensure-typos:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v typos >/dev/null || { echo "'typos' not found. Install with: cargo install typos-cli"; exit 1; }

_ensure-uv:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v uv >/dev/null || { echo "'uv' not found. Install with: brew install uv"; exit 1; }

check: lint test
    @echo "Checks complete."

ci: check security coverage
    @echo "CI checks complete."

coverage: _ensure-uv
    uv run pytest --cov=calendar_analyzer --cov-report=term-missing --cov-report=xml

default:
    @just --list

fix: python-fix toml-fix script-fmt

help-workflows:
    @echo "Common workflows:"
    @echo "  just check         # Run lint, type checks, spelling, TOML checks, script checks, and tests"
    @echo "  just ci            # Run the full local CI workflow"
    @echo "  just coverage      # Generate coverage.xml and terminal coverage"
    @echo "  just fix           # Apply Ruff, Taplo, and shell script auto-fixes"
    @echo "  just security      # Run pip-audit and repository Semgrep rules"
    @echo "  just setup         # Sync uv dev dependencies"
    @echo "  just test          # Run tests only"

lint: python-check toml-check toml-fmt-check spell-check script-check

pip-audit: _ensure-uv
    uv run pip-audit --skip-editable

powershell-check: _ensure-powershell-tools
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.ps1'; git ls-files -z --others --exclude-standard '*.ps1')
    if [ "${#files[@]}" -gt 0 ]; then
        for file in "${files[@]}"; do
            SCRIPT_ANALYZER_PATH="$file" pwsh -NoProfile -Command '$ErrorActionPreference = "Stop"; $results = Invoke-ScriptAnalyzer -Path $env:SCRIPT_ANALYZER_PATH -Severity Warning,Error; if ($results) { $results | Format-List; exit 1 }'
        done
    else
        echo "No PowerShell scripts found to check."
    fi

python-check: _ensure-uv
    uv run ruff format --check .
    uv run ruff check .
    just python-typecheck

python-fix: _ensure-uv
    uv run ruff check . --fix
    uv run ruff format .

python-sync: _ensure-uv
    uv sync --group dev

python-typecheck: _ensure-uv
    uv run ty check calendar_analyzer.py tests --error all

script-check: shell-check powershell-check

script-fmt: shell-fmt

security: pip-audit semgrep

semgrep: _ensure-uv
    uv run semgrep --error --strict --timeout 30 --config semgrep.yaml .

setup: python-sync
    @echo "Setup complete."

shell-check: _ensure-shell-tools
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.sh'; git ls-files -z --others --exclude-standard '*.sh')
    if [ "${#files[@]}" -gt 0 ]; then
        shellcheck "${files[@]}"
        shfmt -d "${files[@]}"
    else
        echo "No shell scripts found to check."
    fi

shell-fmt: _ensure-shell-tools
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.sh'; git ls-files -z --others --exclude-standard '*.sh')
    if [ "${#files[@]}" -gt 0 ]; then
        shfmt -w "${files[@]}"
    else
        echo "No shell scripts found to format."
    fi

spell-check: _ensure-typos
    typos --config typos.toml --force-exclude

test: _ensure-uv
    uv run pytest

toml-check: _ensure-uv
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.toml')
    if [ "${#files[@]}" -gt 0 ]; then
        printf '%s\0' "${files[@]}" | xargs -0 -I {} uv run python -c "import sys, tomllib; exec(\"with open(sys.argv[1], 'rb') as f:\\n    tomllib.load(f)\"); print(f'{sys.argv[1]} is valid TOML')" {}
    else
        echo "No TOML files found to check."
    fi

toml-fix: toml-fmt

toml-fmt: _ensure-taplo
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.toml')
    if [ "${#files[@]}" -gt 0 ]; then
        taplo fmt "${files[@]}"
    else
        echo "No TOML files found to format."
    fi

toml-fmt-check: _ensure-taplo
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.toml')
    if [ "${#files[@]}" -gt 0 ]; then
        taplo fmt --check "${files[@]}"
    else
        echo "No TOML files found to check."
    fi
