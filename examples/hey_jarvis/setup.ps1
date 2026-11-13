# setup.ps1 -- one-time setup for the local "Hey Jarvis" assistant.
#
# Creates a private Python environment next to this script, installs the
# dependencies, downloads the wake-word model, then runs the self-test.
# Nothing is installed system-wide except Python itself, if it is missing.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> Finding Python 3.10-3.13" -ForegroundColor Cyan

function Get-PythonLauncher {
    $candidates = @(
        @{ Exe = "py";     Args = @("-3.13") },
        @{ Exe = "py";     Args = @("-3.12") },
        @{ Exe = "py";     Args = @("-3.11") },
        @{ Exe = "py";     Args = @("-3.10") },
        @{ Exe = "python"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $version = & $candidate.Exe @($candidate.Args + @("-c", "import sys;print('%d.%d'%sys.version_info[:2])")) 2>$null
        } catch { continue }
        if ($version -match '^3\.(10|11|12|13)$') { return $candidate }
    }
    return $null
}

$python = Get-PythonLauncher
if (-not $python) {
    Write-Host "Python 3.10-3.13 not found. Installing Python 3.13 with winget..." -ForegroundColor Yellow
    winget install --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Get-PythonLauncher
    if (-not $python) {
        throw "Python installed but not found yet. Close this window, open a new PowerShell, and run setup.ps1 again."
    }
}

$versionText = & $python.Exe @($python.Args + @("--version")) 2>&1
Write-Host "    using $versionText"

Write-Host "==> Creating a private environment (.venv)" -ForegroundColor Cyan
& $python.Exe @($python.Args + @("-m", "venv", ".venv"))
if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "The virtual environment is incomplete: $venvPython is missing." }

Write-Host "==> Installing dependencies (a few hundred MB, once)" -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Scroll up for the first error." }

Write-Host "==> Downloading the 'hey jarvis' wake-word model" -ForegroundColor Cyan
& $venvPython -c "import openwakeword.utils as u; u.download_models()"
if ($LASTEXITCODE -ne 0) { throw "Could not download the wake-word model." }

Write-Host ""
Write-Host "==> Self-test" -ForegroundColor Cyan
& $venvPython hey_jarvis.py --selftest

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green
Write-Host "Start the assistant by double-clicking run.ps1 (right-click -> Run with PowerShell)."
