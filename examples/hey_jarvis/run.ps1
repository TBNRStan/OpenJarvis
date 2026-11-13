# run.ps1 -- start the local "Hey Jarvis" assistant.
#
#   .\run.ps1            start with a window and a tray icon, no console
#   .\run.ps1 -Console   keep a visible console so errors are readable

param(
    [switch]$Console
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venv = Join-Path $PSScriptRoot ".venv\Scripts"
$pythonw = Join-Path $venv "pythonw.exe"
$python = Join-Path $venv "python.exe"

if (-not (Test-Path $pythonw)) {
    Write-Host "Setup has not been run yet. Running setup.ps1 first..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Setup failed." }
}

if ($Console) {
    & $python hey_jarvis.py
} else {
    # pythonw.exe has no console window: the app appears as a small window
    # plus a tray icon, which is the point.
    Start-Process -FilePath $pythonw -ArgumentList "hey_jarvis.py" -WorkingDirectory $PSScriptRoot
}
