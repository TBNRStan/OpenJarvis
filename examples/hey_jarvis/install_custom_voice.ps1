# install_custom_voice.ps1 -- optional "speak in this voice" backend.
#
# Uses XTTS-v2 locally: you supply a 20-30 second recording of a voice you have
# the rights to use (your own, or someone who has given you permission), and
# Jarvis speaks in it. The recording never leaves this PC.
#
# Two things to know before you start:
#   * The XTTS-v2 model licence is NON-COMMERCIAL. Personal use only.
#   * This is a large install (~2 GB with torch) and speech is slower on CPU.
#
#   .\install_custom_voice.ps1 -Sample my_voice.wav
#   .\install_custom_voice.ps1 -Sample my_voice.wav -Quiet

param(
    [Parameter(Mandatory = $true)][string]$Sample,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "The app is not set up yet. Run setup.ps1 first."
}

$samplePath = Resolve-Path -Path $Sample -ErrorAction SilentlyContinue
if (-not $samplePath) {
    throw "Voice sample not found: $Sample  (a 20-30 second wav of the voice to use)"
}

$env:HEY_JARVIS_VOICE_SAMPLE = $samplePath.Path

Write-Host "==> Installing XTTS (this is a large download)" -ForegroundColor Cyan
& $python -m pip install coqui-tts soundfile
if ($LASTEXITCODE -ne 0) { throw "XTTS installation failed. Scroll up for the first error." }

Write-Host ""
Write-Host "==> Checking the recording" -ForegroundColor Cyan
& $python -c "import voices; print('ready:', voices.prepare_sample())"
if ($LASTEXITCODE -ne 0) {
    throw "That recording could not be read as audio. Use a wav, mp3, flac or m4a file of someone speaking."
}

Write-Host ""
Write-Host "==> Voice backends" -ForegroundColor Cyan
& $python -c "import voices; print(voices.describe())"

if (-not $Quiet) {
    Write-Host ""
    Write-Host "==> First run downloads the XTTS model (~1.8 GB), then speaks a test line" -ForegroundColor Cyan
    & $python -c "import voices; used = voices.speak('Good evening. All systems are online and ready.', backend='xtts'); print('spoken with:', used)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The custom voice could not speak. Jarvis still works with the Kokoro or Windows voice." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done. Start Jarvis with:" -ForegroundColor Green
Write-Host "  .\run.ps1" -ForegroundColor Green
Write-Host "To keep using this sample later, set it for the session first:" -ForegroundColor Gray
Write-Host "  `$env:HEY_JARVIS_VOICE_SAMPLE = `"$($samplePath.Path)`"" -ForegroundColor Gray
