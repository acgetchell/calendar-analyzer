# shellcheck disable=SC2148
# Calendar Analyzer development workflow.

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

_ensure-uv:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v uv >/dev/null || { echo "'uv' not found. Install with: brew install uv"; exit 1; }

_ensure-taplo:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v taplo >/dev/null || { echo "'taplo' not found. Install with: brew install taplo"; exit 1; }

_ensure-typos:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v typos >/dev/null || { echo "'typos' not found. Install with: cargo install typos-cli"; exit 1; }

_ensure-shell-tools:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v shellcheck >/dev/null || { echo "'shellcheck' not found. Install with: brew install shellcheck (macOS) or winget install koalaman.shellcheck (Windows)."; exit 1; }
    command -v shfmt >/dev/null || { echo "'shfmt' not found. Install with: brew install shfmt (macOS) or winget install mvdan.shfmt (Windows)."; exit 1; }

_ensure-powershell-tools:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v pwsh >/dev/null || { echo "'pwsh' not found. Install PowerShell: https://learn.microsoft.com/powershell/"; exit 1; }
    pwsh -NoProfile -Command 'if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) { throw "PSScriptAnalyzer is not installed. Run: Install-Module PSScriptAnalyzer -Scope CurrentUser -Force" }'

help-workflows:
    @echo "Recommended workflow:"
    @echo "  just setup         # Sync uv dev dependencies"
    @echo "  just check         # Run lint, type checks, spelling, TOML checks, and tests"
    @echo "  just fix           # Apply Ruff and Taplo auto-fixes"
    @echo "  just coverage      # Generate coverage.xml and terminal coverage"

setup: python-sync
    @echo "Setup complete."

python-sync: _ensure-uv
    uv sync --group dev

check: lint test
    @echo "Checks complete."

ci: check coverage
    @echo "CI checks complete."

fix: python-fix toml-fix script-fmt

lint: python-check toml-check toml-fmt-check spell-check script-check

python-check: _ensure-uv
    uv run ruff format --check .
    uv run ruff check .
    just python-typecheck

python-fix: _ensure-uv
    uv run ruff check . --fix
    uv run ruff format .

python-typecheck: _ensure-uv
    uv run ty check calendar_analyzer.py tests --error all

test: _ensure-uv
    uv run pytest

coverage: _ensure-uv
    uv run pytest --cov=calendar_analyzer --cov-report=term-missing --cov-report=xml

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

toml-fix: toml-fmt

spell-check: _ensure-typos
    typos --config typos.toml --force-exclude

script-check: shell-check powershell-check

script-fmt: shell-fmt

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
