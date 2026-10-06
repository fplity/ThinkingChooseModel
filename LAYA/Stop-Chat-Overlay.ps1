$ErrorActionPreference = 'Stop'

$entryScript = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'monitor_app.ps1'))
$powershellExe = [System.IO.Path]::GetFullPath((Join-Path $PSHOME 'powershell.exe'))
$expectedEntry = [regex]::Escape($entryScript)
$hosts = @(
    Get-CimInstance -ClassName Win32_Process -Filter "Name='powershell.exe'" -ErrorAction Stop |
        Where-Object {
            $_.ExecutablePath -and
            [string]::Equals([System.IO.Path]::GetFullPath($_.ExecutablePath), $powershellExe, [System.StringComparison]::OrdinalIgnoreCase) -and
            $_.CommandLine -match '(?i)(?:^|\s)-File\s+' -and
            $_.CommandLine -match $expectedEntry
        }
)

if ($hosts.Count -eq 0) {
    Write-Host 'LAYA WPF 小窗当前没有运行。'
    exit 0
}

foreach ($hostInfo in $hosts) {
    # Revalidate the exact process immediately before requesting its WPF window close.
    $current = Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + [int]$hostInfo.ProcessId) -ErrorAction SilentlyContinue
    if (-not $current) { continue }
    $matchesEntry = $current.ExecutablePath -and
        [string]::Equals([System.IO.Path]::GetFullPath($current.ExecutablePath), $powershellExe, [System.StringComparison]::OrdinalIgnoreCase) -and
        $current.CommandLine -match '(?i)(?:^|\s)-File\s+' -and
        $current.CommandLine -match $expectedEntry
    if (-not $matchesEntry) {
        throw 'The candidate process no longer matches the LAYA WPF entrypoint; no process was stopped.'
    }

    $hostProcess = Get-Process -Id ([int]$current.ProcessId) -ErrorAction SilentlyContinue
    if (-not $hostProcess) { continue }
    if (-not $hostProcess.CloseMainWindow()) {
        throw 'Could not request a graceful close from the validated LAYA WPF window.'
    }
    if (-not $hostProcess.WaitForExit(8000)) {
        throw 'The LAYA WPF host did not exit after the graceful close request.'
    }
}

Write-Host 'LAYA WPF 小窗已关闭。'
