#!/usr/bin/env bash
# Bootstrap Calendar Analyzer development tooling on macOS.

set -euo pipefail

run_checks=1

usage() {
	cat <<'EOF'
Usage: scripts/setup-macos.sh [--no-check]

Installs or verifies uv, just, taplo, typos, shellcheck, shfmt, PowerShell,
and PSScriptAnalyzer; ensures Python 3.11 is available through uv; syncs
development dependencies; and runs `just ci` unless --no-check is provided.
EOF
}

while (($#)); do
	case "$1" in
	--no-check)
		run_checks=0
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		echo "Unknown argument: $1" >&2
		usage >&2
		exit 2
		;;
	esac
	shift
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

have() {
	command -v "$1" >/dev/null 2>&1
}

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
	local crate="$1"
	if ! have cargo; then
		return 1
	fi

	echo "  installing with cargo: $crate"
	cargo install --locked "$crate"
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
	curl -LsSf https://astral.sh/uv/install.sh | sh
	export PATH="$HOME/.local/bin:$PATH"
}

ensure_tool() {
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

	if install_with_cargo "$cargo_crate"; then
		export PATH="$HOME/.cargo/bin:$PATH"
		return
	fi

	echo "Missing $command_name. Install Homebrew or Rust, then rerun this script." >&2
	exit 1
}

echo "Bootstrapping Calendar Analyzer development tools..."
ensure_uv
ensure_tool just just just
ensure_tool taplo taplo taplo-cli
ensure_tool typos typos-cli typos-cli
ensure_tool shellcheck shellcheck shellcheck
ensure_tool shfmt shfmt shfmt
ensure_tool pwsh powershell/tap/powershell powershell

echo "Ensuring PSScriptAnalyzer is available..."
pwsh -NoProfile -Command 'if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) { Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force }'

echo "Ensuring Python 3.11 is available through uv..."
uv python install 3.11

echo "Syncing development dependencies..."
uv sync --group dev

if ((run_checks)); then
	echo "Running local CI..."
	just ci
else
	echo "Skipping checks. Run 'just ci' when ready."
fi

echo "Setup complete."
