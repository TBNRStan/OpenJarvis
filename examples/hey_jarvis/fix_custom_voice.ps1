# fix_custom_voice.ps1 -- repair the custom-voice engine (thin launcher).
#
# The repair itself lives in fix_custom_voice.py: subprocess work in Python is
# predictable, where PowerShell turns a failing command's stderr into a fatal
# error that kills the script. This wrapper only finds the environment.
#
#   .\fix_custom_voice.ps1 -Sample .\my_takes
#   .\fix_custom_voice.ps1 -Sample .\my_takes -AcceptTerms
#   .\fix_custom_voice.ps1 -Check            # show versions, change nothing

param(
    [string]$Sample = ".\my_takes",
    [switch]$AcceptTerms,
    [switch]$Check
)

Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "The app is not set up yet. Run setup.ps1 first."
}

$arguments = @("fix_custom_voice.py", "--sample", $Sample)
if ($AcceptTerms) { $arguments += "--accept-terms" }
if ($Check) { $arguments += "--check" }

& $python @arguments
exit $LASTEXITCODE
