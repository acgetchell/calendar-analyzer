# Setup Scripts

This directory contains platform setup scripts for local development:

- `setup-macos.sh` installs or verifies the macOS toolchain.
- `setup-windows.ps1` installs or verifies the Windows toolchain.

Both scripts sync development dependencies with `uv sync --group dev` and run `just ci` by default. Use `--no-check` on macOS or `-NoCheck` on Windows to install and sync dependencies without running checks.

## Script Checks

Script checks are part of the standard project workflow:

```bash
just script-check
```

`just script-check` runs:

- `shellcheck` on tracked and untracked `*.sh` files.
- `shfmt -d` on tracked and untracked `*.sh` files to verify formatting.
- `PSScriptAnalyzer` on tracked and untracked `*.ps1` files.

These checks also run through:

```bash
just check
just ci
```

## Script Formatting

Shell scripts can be formatted automatically:

```bash
just script-fmt
```

`just script-fmt` runs `shfmt -w` on tracked and untracked `*.sh` files. PowerShell scripts are checked with `PSScriptAnalyzer`, but this project does not currently apply automatic PowerShell formatting.

## Direct Tool Commands

The `just` recipes are the preferred interface, but the direct commands are:

```bash
shellcheck scripts/setup-macos.sh
shfmt -d scripts/setup-macos.sh
```

```powershell
Invoke-ScriptAnalyzer -Path scripts/setup-windows.ps1 -Severity Warning,Error
```

## Required Tools

The setup scripts install or verify:

- `uv`
- `just`
- `taplo`
- `typos`
- `shellcheck`
- `shfmt`
- PowerShell (`pwsh`)
- `PSScriptAnalyzer`

On Windows, run `just` from Git Bash or another Bash-compatible shell because the project `justfile` uses Bash recipes.
