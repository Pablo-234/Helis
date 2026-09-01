[CmdletBinding()]
param(
    [string] $RepoRoot = (Join-Path $env:USERPROFILE "Helis"),

    [string] $EnvFile = (Join-Path $env:USERPROFILE ".config\helis\helis.env"),

    [switch] $ConfirmPublicNetworkReads
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ConfirmPublicNetworkReads) {
    throw "Re-run with -ConfirmPublicNetworkReads to allow the bounded pilot to read configured public market sources and call the local model."
}

function Invoke-CheckedExecutable {
    param(
        [Parameter(Mandatory = $true)][string] $Executable,
        [Parameter(Mandatory = $true)][string[]] $Arguments
    )

    & $Executable @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Executable $($Arguments -join ' ') failed with exit code $exitCode"
    }
}

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
$envLoader = Join-Path $resolvedRepo "deploy\windows\Import-HelisEnv.ps1"
$live = Join-Path $resolvedRepo ".venv\Scripts\helis-live.exe"
$operator = Join-Path $resolvedRepo ".venv\Scripts\helis-operator.exe"
foreach ($required in @($envLoader, $live, $operator)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required HELIS file does not exist: $required"
    }
}

Push-Location -LiteralPath $resolvedRepo
try {
    & $envLoader -EnvFile $resolvedEnv
    Invoke-CheckedExecutable -Executable $live -Arguments @("bootstrap")
    Invoke-CheckedExecutable -Executable $live -Arguments @("model-status")
    Invoke-CheckedExecutable -Executable $live -Arguments @("model-smoke")
    Invoke-CheckedExecutable -Executable $live -Arguments @("doctor", "--probe-model")
    Invoke-CheckedExecutable -Executable $live -Arguments @("pilot")
    Invoke-CheckedExecutable -Executable $live -Arguments @("pilot-status")
    Invoke-CheckedExecutable -Executable $operator -Arguments @("inbox")
} finally {
    Pop-Location
}

Write-Host "HELIS controlled pilot completed. Review the persisted report and operator inbox above."
