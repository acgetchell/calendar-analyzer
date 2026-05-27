# shellcheck disable=SC2148
# Calendar Analyzer development workflow.

set shell := ["bash", "-euo", "pipefail", "-c"]

_ensure-powershell-tools:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v pwsh >/dev/null || { echo "'pwsh' not found. Install PowerShell: https://learn.microsoft.com/powershell/"; exit 1; }
    pwsh -NoProfile -Command 'if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) { throw "PSScriptAnalyzer is not installed. Run: Install-Module PSScriptAnalyzer -Scope CurrentUser -Force" }'

_ensure-rumdl:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v rumdl >/dev/null || { echo "'rumdl' not found. Install with: brew install rumdl (macOS) or cargo install rumdl"; exit 1; }

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

_ensure-zizmor:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v zizmor >/dev/null || { echo "'zizmor' not found. Install with: cargo install zizmor"; exit 1; }

check: lint test
    @echo "Checks complete."

ci: check security
    @echo "CI checks complete."

coverage: _ensure-uv
    uv run pytest --cov=calendar_analyzer --cov-report=term-missing --cov-report=xml

default:
    @just --list

fix: python-fix toml-fix markdown-fix script-fmt

help-workflows:
    @echo "Common workflows:"
    @echo "  just check         # Run lint, type checks, spelling, TOML checks, script checks, and tests"
    @echo "  just ci            # Run local CI checks without generating coverage"
    @echo "  just coverage      # Generate coverage.xml and terminal coverage"
    @echo "  just fix           # Apply Ruff, Taplo, and shell script auto-fixes"
    @echo "  just markdown-check # Check Markdown formatting and style"
    @echo "  just run [args]    # Run the calendar analyzer"
    @echo "  just security      # Run pip-audit, Semgrep, and zizmor rules"
    @echo "  just setup         # Install or verify development tools and dependencies"
    @echo "  just test          # Run tests only"
    @echo "  just zizmor        # Run GitHub Actions security analysis"

lint: python-check toml-check toml-fmt-check markdown-check spell-check script-check

markdown-check: _ensure-rumdl
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.md')
    if [ "${#files[@]}" -gt 0 ]; then
        printf '%s\0' "${files[@]}" | xargs -0 -n100 rumdl check
    else
        echo "No Markdown files found to check."
    fi

markdown-fix: _ensure-rumdl
    #!/usr/bin/env bash
    set -euo pipefail
    files=()
    while IFS= read -r -d '' file; do
        files+=("$file")
    done < <(git ls-files -z '*.md')
    if [ "${#files[@]}" -gt 0 ]; then
        printf '%s\0' "${files[@]}" | xargs -0 -n100 rumdl check --fix
    else
        echo "No Markdown files found to format."
    fi

markdown-lint: markdown-check

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

security: pip-audit semgrep zizmor

semgrep: _ensure-uv
    uv run semgrep --error --strict --timeout 30 --config semgrep.yaml .

run *args: _ensure-uv
    uv run calendar-analyzer {{args}}

setup: setup-tools
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Setting up Calendar Analyzer development environment..."
    echo "Ensuring Python 3.11 is available through uv..."
    uv python install 3.11
    echo "Syncing development dependencies..."
    uv sync --group dev
    echo "Setup complete. Run 'just ci' when ready."

setup-tools:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "Ensuring development tools required by just recipes are installed..."
    echo ""

    os="$(uname -s || true)"
    have() { command -v "$1" >/dev/null 2>&1; }

    install_with_brew() {
        local formula="$1"
        if ! have brew; then
            return 1
        fi
        if brew list --versions "$formula" >/dev/null 2>&1; then
            echo "  ok: $formula"
        else
            echo "  installing: $formula"
            HOMEBREW_NO_AUTO_UPDATE=1 brew install "$formula"
        fi
    }

    install_with_cargo() {
        local command_name="$1"
        local crate="$2"
        if have "$command_name"; then
            echo "  ok: $command_name"
            return
        fi
        if ! have cargo; then
            echo "Missing $command_name and cargo is not available. Install Rust from https://rustup.rs, then rerun 'just setup'." >&2
            exit 1
        fi
        echo "  installing with cargo: $crate"
        cargo install --locked "$crate"
        export PATH="$HOME/.cargo/bin:$PATH"
    }

    ensure_uv() {
        if have uv; then
            echo "  ok: uv"
            return
        fi
        if install_with_brew uv; then
            return
        fi
        echo "  installing uv with the official installer"
        local uv_installer
        uv_installer="$(mktemp "${TMPDIR:-/tmp}/uv-install.XXXXXX")"
        curl -LsSf https://astral.sh/uv/install.sh -o "$uv_installer"
        if ! sh "$uv_installer"; then
            rm -f "$uv_installer"
            return 1
        fi
        rm -f "$uv_installer"
        export PATH="$HOME/.local/bin:$PATH"
    }

    ensure_brew_or_cargo_tool() {
        local command_name="$1"
        local brew_formula="$2"
        local cargo_crate="$3"
        if have "$command_name"; then
            echo "  ok: $command_name"
            return
        fi
        if install_with_brew "$brew_formula"; then
            return
        fi
        install_with_cargo "$command_name" "$cargo_crate"
    }

    ensure_system_tool() {
        local command_name="$1"
        local brew_formula="$2"
        local install_hint="$3"
        if have "$command_name"; then
            echo "  ok: $command_name"
            return
        fi
        if install_with_brew "$brew_formula"; then
            return
        fi
        echo "Missing $command_name. $install_hint" >&2
        exit 1
    }

    ensure_uv
    ensure_brew_or_cargo_tool just just just
    ensure_brew_or_cargo_tool rumdl rumdl rumdl
    ensure_brew_or_cargo_tool taplo taplo taplo-cli
    ensure_brew_or_cargo_tool typos typos-cli typos-cli
    ensure_brew_or_cargo_tool zizmor zizmor zizmor
    ensure_system_tool shellcheck shellcheck "Install with: brew install shellcheck (macOS) or winget install koalaman.shellcheck (Windows)."
    ensure_system_tool shfmt shfmt "Install with: brew install shfmt (macOS) or winget install mvdan.shfmt (Windows)."
    ensure_system_tool pwsh powershell/tap/powershell "Install PowerShell: https://learn.microsoft.com/powershell/"

    echo "Ensuring PSScriptAnalyzer is available..."
    pwsh -NoProfile -Command 'if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) { Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force }'

    echo ""
    echo "Verifying required commands are available..."
    missing=0
    cmds=(uv just rumdl taplo typos shellcheck shfmt pwsh zizmor)
    for cmd in "${cmds[@]}"; do
        if have "$cmd"; then
            echo "  ok: $cmd"
        else
            echo "  missing: $cmd"
            missing=1
        fi
    done
    if [ "$missing" -ne 0 ]; then
        echo ""
        if [[ "$os" == "Darwin" ]]; then
            echo "Install Homebrew or Rust, then rerun 'just setup'." >&2
        else
            echo "Install the missing tools with your system package manager, then rerun 'just setup'." >&2
        fi
        exit 1
    fi

    echo "Tooling setup complete."

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
        printf '%s\0' "${files[@]}" | xargs -0 -I {} uv run python -c "import pathlib, sys, tomllib; path = pathlib.Path(sys.argv[1]); tomllib.loads(path.read_text(encoding='utf-8')); print(f'{path} is valid TOML')" {}
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

zizmor: _ensure-zizmor
    zizmor .github
