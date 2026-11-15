# install_voice.ps1 -- optional neural voice for Jarvis.
#
# Windows' built-in voice is instant but robotic and usually American. This
# installs Kokoro, whose British male voices (bm_george by default) are the
# closest free approximation of the film's Jarvis. One-time download of a few
# hundred MB, then everything runs offline on your CPU.
#
#   .\install_voice.ps1                 # British male, bm_george
#   .\install_voice.ps1 -Voice bm_lewis # alternative British male
#   .\install_voice.ps1 -Quiet          # install only, no test line

param(
    [string]$Voice = "bm_george",
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "The app is not set up yet. Run setup.ps1 first."
}

Write-Host "==> Installing the neural voice (kokoro + soundfile)" -ForegroundColor Cyan
& $python -m pip install "kokoro>=0.9" soundfile
if ($LASTEXITCODE -ne 0) { throw "Voice installation failed. Scroll up for the first error." }

$env:HEY_JARVIS_KOKORO_VOICE = $Voice

Write-Host ""
Write-Host "==> Available voice backends" -ForegroundColor Cyan
& $python -c "import voices; print(voices.describe())"

if (-not $Quiet) {
    Write-Host ""
    Write-Host "==> Speaking a test line (voice: $Voice)" -ForegroundColor Cyan
    & $python -c "import voices; used = voices.speak('Good evening. All systems are online and ready.', backend='kokoro'); print('spoken with:', used)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The neural voice could not speak. Jarvis still works: it falls back to the Windows voice." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done. Start Jarvis with run.ps1 -- it will use this voice automatically." -ForegroundColor Green
