# rebuild_venv.ps1 -- recreate .venv with Python 3.12.
#
# The neural voice (kokoro) publishes wheels only for Python 3.10-3.12. An
# environment built with 3.13 or newer installs the assistant fine but can
# never install the voice, so pip reports "no matching distribution found".
#
# This finds a supported Python (installing 3.12 with winget if needed), moves
# the old environment to .venv.old, builds a fresh one, reinstalls everything
# and re-runs the self-test. Nothing else on the machine is touched.
#
#   .\rebuild_venv.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Get-SupportedPython {
    foreach ($minor in @("3.12", "3.11", "3.10")) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            try {
                $version = & py "-$minor" -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            } catch {
                $version = $null
            }
            if ($version -match '^3\.(10|11|12)$') {
                return @{ Exe = "py"; Args = @("-$minor"); Version = $version }
            }
        }
        $installed = "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python{0}\python.exe" -f ($minor -replace '\.', '')
        if (Test-Path $installed) {
            $version = & $installed -c "import sys;print('%d.%d'%sys.version_info[:2])"
            if ($version -match '^3\.(10|11|12)$') {
                return @{ Exe = $installed; Args = @(); Version = $version }
            }
        }
    }
    return $null
}

$python = Get-SupportedPython
if (-not $python) {
    Write-Host "==> Python 3.12 is needed for the neural voice; installing it with winget" -ForegroundColor Yellow
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Get-SupportedPython
    if (-not $python) {
        throw "Python 3.12 was installed but is not visible yet. Close this window, open a new PowerShell, and run rebuild_venv.ps1 again."
    }
}

Write-Host "==> Using Python $($python.Version)" -ForegroundColor Cyan

if (Test-Path ".venv") {
    Write-Host "==> Moving the current environment aside to .venv.old" -ForegroundColor Cyan
    if (Test-Path ".venv.old") { Remove-Item -Recurse -Force ".venv.old" }
    Rename-Item -Path ".venv" -NewName ".venv.old"
}

Write-Host "==> Creating a fresh environment" -ForegroundColor Cyan
& $python.Exe @($python.Args + @("-m", "venv", ".venv"))
if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "The environment is incomplete: $venvPython is missing." }

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
Write-Host "Done. Next: install_voice.ps1" -ForegroundColor Green
Write-Host "The old environment is in .venv.old -- delete that folder once Jarvis is working." -ForegroundColor Gray
