param(
  [string]$PythonExecutable = 'C:/Users/0120c/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',
  [int]$NeuronCount = 384
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$taskTemp = Join-Path $projectRoot '.work-tmp'
New-Item -ItemType Directory -Force -Path $taskTemp | Out-Null
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$env:PYTHONUTF8 = '1'
$venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
  & $PythonExecutable -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw 'Could not create workspace Python environment.' }
}
& $venvPython -m pip install --disable-pip-version-check -r python/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install brain dependencies.' }
& $venvPython python/configure_brian.py
if ($LASTEXITCODE -ne 0) { throw 'Could not configure Brian2 workspace cache.' }
& $venvPython python/prepare_data.py --neurons $NeuronCount
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare real FlyWire data.' }
Write-Output "Python executable: $venvPython"
Write-Output "Brain data: $(Join-Path $projectRoot 'data/flywire/subset.json')"
