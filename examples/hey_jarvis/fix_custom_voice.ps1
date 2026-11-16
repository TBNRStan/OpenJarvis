# fix_custom_voice.ps1 -- repair the custom-voice engine in an existing .venv.
#
# The symptom is a torchcodec DLL error such as
#   "Could not load this library: ...\torchcodec\libtorchcodec_core4.dll"
# Every torchcodec release is built against one exact PyTorch version, so any
# mismatch fails at load time. PyTorch 2.8 and older decode audio through
# torchaudio instead and never touch torchcodec, so this pins that pair and
# removes torchcodec entirely.
#
#   .\fix_custom_voice.ps1 -Sample .\my_takes
#   .\fix_custom_voice.ps1 -Sample .\my_takes -AcceptTerms

param(
    [string]$Sample = ".\my_takes",
    [switch]$AcceptTerms
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "The app is not set up yet. Run setup.ps1 first."
}

Write-Host "==> What is installed now" -ForegroundColor Cyan
# Diagnostics may legitimately fail (a package missing, writing to stderr).
# With $ErrorActionPreference = 'Stop' PowerShell 5.1 turns that into a fatal
# NativeCommandError, so relax it just for these three probes.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python -c "import torch; print('    torch      ', torch.__version__)"
& $python -c "import torchcodec; print('    torchcodec ', torchcodec.__version__)"
& $python -c "import torchaudio; print('    torchaudio ', torchaudio.__version__)"
$ErrorActionPreference = $previousPreference

Write-Host "==> Removing torchcodec and the engine" -ForegroundColor Cyan
& $python -m pip uninstall -y torchcodec coqui-tts coqui-tts-trainer

Write-Host "==> Installing PyTorch 2.8.0 (no torchcodec needed)" -ForegroundColor Cyan
& $python -m pip install "torch==2.8.0" "torchaudio==2.8.0"
if ($LASTEXITCODE -ne 0) { throw "PyTorch 2.8.0 could not be installed. Scroll up for the first error." }

Write-Host "==> Installing the voice engine" -ForegroundColor Cyan
& $python -m pip install "coqui-tts==0.28.0" "transformers<5" soundfile
if ($LASTEXITCODE -ne 0) { throw "coqui-tts could not be installed. Scroll up for the first error." }

Write-Host "==> Checking the engine imports" -ForegroundColor Cyan
$installedTorch = & $python -c "import torch; print(torch.__version__.split('+')[0])"
if ([version]$installedTorch -ge [version]"2.9") {
    throw "PyTorch $installedTorch is still installed; this needs 2.8.x. Run: .\.venv\Scripts\python.exe -m pip install --force-reinstall \"torch==2.8.0\" \"torchaudio==2.8.0\""
}
Write-Host "    torch $installedTorch (no torchcodec needed)" -ForegroundColor Gray
& $python -c "import torch, torchaudio; from TTS.api import TTS; print('    torch', torch.__version__, '- engine loaded')"
if ($LASTEXITCODE -ne 0) { throw "The engine still will not import. Scroll up for the first error." }

$samplePath = Resolve-Path -Path $Sample -ErrorAction SilentlyContinue
if ($samplePath) {
    $env:HEY_JARVIS_VOICE_SAMPLE = $samplePath.Path
    Write-Host "    sample: $($samplePath.Path)" -ForegroundColor Gray
} else {
    Write-Host "    sample folder not found: $Sample (speaking with the default sample would fail)" -ForegroundColor Yellow
}
if ($AcceptTerms) {
    # Same effect as answering "y": consent to the non-commercial CPML.
    $env:COQUI_TOS_AGREED = "1"
}

Write-Host ""
Write-Host "==> Speaking a test line in the custom voice" -ForegroundColor Cyan
& $python -c "import voices; print('engine:', voices.xtts_status()); print('spoken with:', voices.speak('Good evening. All systems are online and ready.', backend='xtts'))"
if ($LASTEXITCODE -ne 0) {
    throw "The custom voice could not speak. Scroll up: the line above names the cause."
}

Write-Host ""
Write-Host "Done. Use it with:" -ForegroundColor Green
Write-Host "  .\run.ps1 --voice xtts --voice-sample `"$Sample`"" -ForegroundColor Green
