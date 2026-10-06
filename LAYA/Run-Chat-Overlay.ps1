param(
    [string]$TargetContact = '陈颢予'
)

$ErrorActionPreference = 'Stop'
$TargetContact = $TargetContact.Trim()
if ([string]::IsNullOrWhiteSpace($TargetContact) -or $TargetContact.Length -gt 80 -or $TargetContact.IndexOfAny([char[]]([char]0, [char]9, [char]10, [char]13, [char]34)) -ge 0) {
    throw 'TargetContact must be a non-empty chat name of at most 80 characters and cannot contain quotes.'
}

$entryScript = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'monitor_app.ps1'))
$powershellExe = [System.IO.Path]::GetFullPath((Join-Path $PSHOME 'powershell.exe'))
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$runtime = Join-Path $PSScriptRoot 'laya_runtime.py'

if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) {
    throw 'Windows PowerShell is unavailable.'
}
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf) -or -not (Test-Path -LiteralPath $runtime -PathType Leaf)) {
    throw 'Laya environment is missing. Run setup.ps1 first.'
}

# Refuse a duplicate only when both executable and exact WPF entrypoint match.
$expectedEntry = [regex]::Escape($entryScript)
$running = Get-CimInstance -ClassName Win32_Process -Filter "Name='powershell.exe'" -ErrorAction Stop |
    Where-Object {
        $_.ExecutablePath -and
        [string]::Equals([System.IO.Path]::GetFullPath($_.ExecutablePath), $powershellExe, [System.StringComparison]::OrdinalIgnoreCase) -and
        $_.CommandLine -match '(?i)(?:^|\s)-File\s+' -and
        $_.CommandLine -match $expectedEntry
    }
if ($running) {
    throw 'LAYA WPF 小窗已经运行。'
}

# Keep the existing local cache environment contract; no install or migration.
$env:HF_HOME = Join-Path $PSScriptRoot 'models\huggingface'
$env:RAPIDOCR_HOME = Join-Path $PSScriptRoot 'models\ocr'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'

$arguments = @(
    '-NoProfile',
    '-STA',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"' + $entryScript + '"'),
    '-TargetContact', ('"' + $TargetContact + '"')
)
$hostProcess = Start-Process -FilePath $powershellExe -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
Write-Host ('LAYA WPF host started (PID ' + $hostProcess.Id + ').')
