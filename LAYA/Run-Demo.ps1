param([string]$InputFile = (Join-Path $PSScriptRoot 'demo-request.json'))

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Laya environment is missing. Run setup.ps1 first.' }
if (-not (Test-Path -LiteralPath $InputFile -PathType Leaf)) { throw "Request JSON not found: $InputFile" }

$env:HF_HOME = Join-Path $PSScriptRoot 'models\huggingface'
$env:PYTHONUNBUFFERED = '1'
& $python (Join-Path $PSScriptRoot 'laya_client.py') '--input' $InputFile
if ($LASTEXITCODE -ne 0) { throw "Laya request failed (exit code $LASTEXITCODE)." }
