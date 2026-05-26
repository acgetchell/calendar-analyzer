# Bootstrap Calendar Analyzer development tooling on Windows.

[CmdletBinding()]
param(
    [switch]$NoCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$InformationPreference = "Continue"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

function Test-Command {
    param([Parameter(Mandatory)][string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Add-PathEntry {
    param([Parameter(Mandatory)][string]$Path)
    if ((Test-Path $Path) -and ($env:Path -notlike "*$Path*")) {
        $env:Path = "$Path;$env:Path"
    }
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][string]$Name
    )

    if (-not (Test-Command winget)) {
        return $false
    }

    Write-Information "  installing: $Name ($Id)"
    winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements
    return $true
}

function Install-Cargo {
    if (Test-Command cargo) {
        Write-Information "  ok: cargo"
        return
    }

    if (-not (Install-WingetPackage -Id "Rustlang.Rustup" -Name "Rustup")) {
        throw "Missing cargo. Install Rust from https://rustup.rs, then rerun this script."
    }

    Add-PathEntry (Join-Path $env:USERPROFILE ".cargo\bin")
    if (-not (Test-Command cargo)) {
        throw "Rustup was installed, but cargo is not on PATH yet. Open a new PowerShell window and rerun this script."
    }
}

function Install-Uv {
    if (Test-Command uv) {
        Write-Information "  ok: uv"
        return
    }

    if (-not (Install-WingetPackage -Id "astral-sh.uv" -Name "uv")) {
        throw "Missing uv. Install winget or uv from https://docs.astral.sh/uv/getting-started/installation/, then rerun this script."
    }

    Add-PathEntry (Join-Path $env:USERPROFILE ".local\bin")
    if (-not (Test-Command uv)) {
        throw "uv was installed, but is not on PATH yet. Open a new PowerShell window and rerun this script."
    }
}

function Install-GitBash {
    if (Test-Command bash) {
        Write-Information "  ok: bash"
        return
    }

    if (-not (Install-WingetPackage -Id "Git.Git" -Name "Git for Windows")) {
        throw "Missing bash. Install Git for Windows, then rerun this script."
    }

    Add-PathEntry "C:\Program Files\Git\bin"
    if (-not (Test-Command bash)) {
        throw "Git was installed, but bash is not on PATH yet. Open a new PowerShell window and rerun this script."
    }
}

function Install-WingetTool {
    param(
        [Parameter(Mandatory)][string]$CommandName,
        [Parameter(Mandatory)][string]$WingetId,
        [Parameter(Mandatory)][string]$DisplayName
    )

    if (Test-Command $CommandName) {
        Write-Information "  ok: $CommandName"
        return
    }

    if (-not (Install-WingetPackage -Id $WingetId -Name $DisplayName)) {
        throw "Missing $CommandName. Install $DisplayName, then rerun this script."
    }

    if (-not (Test-Command $CommandName)) {
        throw "$DisplayName was installed, but $CommandName is not on PATH yet. Open a new PowerShell window and rerun this script."
    }
}

function Install-CargoTool {
    param(
        [Parameter(Mandatory)][string]$CommandName,
        [Parameter(Mandatory)][string]$CrateName
    )

    if (Test-Command $CommandName) {
        Write-Information "  ok: $CommandName"
        return
    }

    Install-Cargo
    Write-Information "  installing with cargo: $CrateName"
    cargo install --locked $CrateName
    Add-PathEntry (Join-Path $env:USERPROFILE ".cargo\bin")

    if (-not (Test-Command $CommandName)) {
        throw "$CommandName was installed, but is not on PATH yet. Open a new PowerShell window and rerun this script."
    }
}

function Install-PSScriptAnalyzer {
    if (Get-Module -ListAvailable -Name PSScriptAnalyzer) {
        Write-Information "  ok: PSScriptAnalyzer"
        return
    }

    Write-Information "  installing: PSScriptAnalyzer"
    Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force
}

Write-Information "Bootstrapping Calendar Analyzer development tools..."
Install-Uv
Install-GitBash
Install-WingetTool -CommandName "shellcheck" -WingetId "koalaman.shellcheck" -DisplayName "ShellCheck"
Install-WingetTool -CommandName "shfmt" -WingetId "mvdan.shfmt" -DisplayName "shfmt"
Install-CargoTool -CommandName "just" -CrateName "just"
Install-CargoTool -CommandName "taplo" -CrateName "taplo-cli"
Install-CargoTool -CommandName "typos" -CrateName "typos-cli"
Install-PSScriptAnalyzer

Write-Information "Ensuring Python 3.11 is available through uv..."
uv python install 3.11

Write-Information "Syncing development dependencies..."
uv sync --group dev

if (-not $NoCheck) {
    Write-Information "Running local CI..."
    just ci
}
else {
    Write-Information "Skipping checks. Run 'just ci' when ready."
}

Write-Information "Setup complete."
