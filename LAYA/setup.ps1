$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    py -3.14 -m venv (Join-Path $root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python 3.14 virtual environment.' }
}

$env:HF_HOME = Join-Path $root 'models\huggingface'
$env:RAPIDOCR_HOME = Join-Path $root 'models\ocr'
New-Item -ItemType Directory -Force -Path $env:HF_HOME | Out-Null
New-Item -ItemType Directory -Force -Path $env:RAPIDOCR_HOME | Out-Null
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
& $python -m pip install --index-url 'https://pypi.tuna.tsinghua.edu.cn/simple' --extra-index-url 'https://download.pytorch.org/whl/cu132' -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Laya installation failed.' }
& $python -m pip install -r (Join-Path $root 'requirements-monitor.txt')
if ($LASTEXITCODE -ne 0) { throw 'Local OCR installation failed.' }

Write-Host 'Laya and local OCR are ready. Model files are in:' $PSScriptRoot 'models'
