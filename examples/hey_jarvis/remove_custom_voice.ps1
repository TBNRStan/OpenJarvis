# remove_custom_voice.ps1 -- take the optional cloned voice back out.
#
# The custom voice pulls in coqui-tts, torchcodec and a 1.8 GB model. If it is
# not working, this removes the parts only it needs and leaves the rest of the
# assistant untouched: PyTorch stays because Kokoro's British voice uses it.
#
#   .\remove_custom_voice.ps1                 # remove packages, keep the model
#   .\remove_custom_voice.ps1 -DeleteModel    # also delete the 1.8 GB cache

param(
    [switch]$DeleteModel
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "The app is not set up yet: $python is missing."
}

Write-Host "==> Removing the custom-voice packages" -ForegroundColor Cyan
& $python -m pip uninstall -y coqui-tts coqui-tts-trainer torchcodec
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip reported a problem; the packages may already be gone." -ForegroundColor Yellow
}

$cache = Join-Path $env:USERPROFILE ".cache\huggingface\hub\models--coqui--XTTS-v2"
if (Test-Path $cache) {
    $mb = [math]::Round((Get-ChildItem $cache -Recurse -Force -ErrorAction SilentlyContinue |
                         Measure-Object Length -Sum).Sum / 1MB)
    Write-Host ""
    Write-Host "==> Cloned-voice model cache: $mb MB at $cache" -ForegroundColor Cyan
    if ($DeleteModel) {
        Remove-Item $cache -Recurse -Force
        Write-Host "    deleted ($mb MB reclaimed)" -ForegroundColor Green
    } else {
        Write-Host "    kept. Re-run with -DeleteModel to reclaim it." -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "==> Voices now available" -ForegroundColor Cyan
$env:HEY_JARVIS_VOICE_SAMPLE = ""
& $python -c "import voices; print(voices.describe())"

Write-Host ""
Write-Host "Done. Jarvis uses the Kokoro British voice." -ForegroundColor Green
Write-Host "Remove HEY_JARVIS_VOICE_SAMPLE from your environment if you set it permanently." -ForegroundColor Gray
