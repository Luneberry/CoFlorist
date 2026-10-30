param([string]$PythonExecutable = 'py')
$ErrorActionPreference = 'Stop'
$rootForVoice = Split-Path -Parent $PSScriptRoot
$pythonForVoice = Join-Path $rootForVoice '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonForVoice)) {
    & $PythonExecutable -m venv (Join-Path $rootForVoice '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
& $pythonForVoice -m pip install -r (Join-Path $rootForVoice 'python\requirements-voice.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install speech recognition dependencies.' }
& $pythonForVoice (Join-Path $rootForVoice 'python\voice_transcribe.py') --prepare
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Whisper model.' }
Write-Output 'Local voice ready. Run npm start and open http://localhost:4317'
