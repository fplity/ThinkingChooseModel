param(
    [switch]$ListModels,
    [string]$InputFile = (Join-Path $PSScriptRoot 'demo-request.json')
)

$ErrorActionPreference = 'Stop'
$enteredKey = $false
$plainKey = $null
$secureKey = $null
$bstr = [IntPtr]::Zero

try {
    if ([string]::IsNullOrWhiteSpace($env:TYPESAFE_API_KEY)) {
        $secureKey = Read-Host 'Paste your TypeSafe API key (input is hidden and will not be saved)' -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        if ([string]::IsNullOrWhiteSpace($plainKey)) { throw 'A TypeSafe API key is required to call Jev.' }
        $env:TYPESAFE_API_KEY = $plainKey
        $enteredKey = $true
    }

    $pythonArgs = @((Join-Path $PSScriptRoot 'jev_client.py'))
    if ($ListModels) {
        $pythonArgs += '--list-models'
    }
    else {
        if (-not (Test-Path -LiteralPath $InputFile -PathType Leaf)) { throw "Request JSON not found: $InputFile" }
        $pythonArgs += @('--input', $InputFile)
    }

    py -3.14 @pythonArgs
    if ($LASTEXITCODE -ne 0) { throw "Jev request failed (exit code $LASTEXITCODE)." }
}
finally {
    if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if ($enteredKey) { $env:TYPESAFE_API_KEY = $null }
    $plainKey = $null
    if ($secureKey) { $secureKey.Dispose() }
}
