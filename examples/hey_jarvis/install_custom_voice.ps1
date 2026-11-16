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
#   .\install_custom_voice.ps1 -Sample .\my_takes        # a folder of takes
#   .\install_custom_voice.ps1 -Sample my_voice.wav -Quiet
#   .\install_custom_voice.ps1 -Sample my_voice.wav -AcceptTerms

param(
    [Parameter(Mandatory = $true)][string]$Sample,
    [switch]$Quiet,
    # Records that you accept the XTTS-v2 model licence (CPML, non-commercial)
    # so the first download does not stop at an interactive y/n prompt.
    [switch]$AcceptTerms
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "The app is not set up yet. Run setup.ps1 first."
}

$samplePath = Resolve-Path -Path $Sample -ErrorAction SilentlyContinue
if (-not $samplePath) {
    throw "Voice sample not found: $Sample  (a recording of the voice to use, or a folder of takes)"
}

$env:HEY_JARVIS_VOICE_SAMPLE = $samplePath.Path

if ($AcceptTerms) {
    # Same effect as answering "y": consent to the non-commercial CPML.
    $env:COQUI_TOS_AGREED = "1"
    Write-Host "    licence: accepting the non-commercial CPML for this run" -ForegroundColor Gray
}

Write-Host "==> Installing PyTorch audio support (this is the large download)" -ForegroundColor Cyan
# coqui-tts needs torch *and* torchaudio; the Kokoro install only brings torch,
# so torchaudio is normally missing and the engine fails at import without it.
& $python -m pip install torch torchaudio
if ($LASTEXITCODE -ne 0) { throw "PyTorch installation failed. Scroll up for the first error." }
& $python -c "import torch, torchaudio; print('    torch', torch.__version__, '/ torchaudio', torchaudio.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "PyTorch is installed but torchaudio will not import. Scroll up for the first error."
}

Write-Host "==> Installing the voice engine (coqui-tts + codec)" -ForegroundColor Cyan
# transformers 5.x breaks coqui-tts at import time, so keep it on the 4.x line.
# The codec extra brings torchcodec, required for audio IO from PyTorch 2.9 on.
& $python -m pip install "coqui-tts[codec]" "transformers<5" soundfile
if ($LASTEXITCODE -ne 0) { throw "XTTS installation failed. Scroll up for the first error." }

function Test-VoiceEngine {
    & $python -c "from TTS.api import TTS" 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-VoiceEngine)) {
    Write-Host "==> The engine needs FFmpeg to decode audio; installing it" -ForegroundColor Yellow
    winget install --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Test-VoiceEngine)) {
        throw "The voice engine still will not load. Run this to see why: .\.venv\Scripts\python.exe -c `"from TTS.api import TTS`""
    }
}

Write-Host ""
Write-Host "==> Checking the recording" -ForegroundColor Cyan
& $python -c "import voices; print('ready:', voices.prepare_sample())"
if ($LASTEXITCODE -ne 0) {
    throw "That recording could not be read as audio. Use a wav, mp3, flac or m4a file of someone speaking."
}

Write-Host ""
Write-Host "==> Voice backends" -ForegroundColor Cyan
& $python -c "import voices; print(voices.describe())"
& $python -c "import voices; print('custom voice:', voices.xtts_status())"

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
