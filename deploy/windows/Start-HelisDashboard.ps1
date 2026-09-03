[CmdletBinding()]
param(
    [string] $RepoRoot = (Join-Path $env:USERPROFILE "Helis"),

    [int] $Port = 8765
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (($Port -lt 1024) -or ($Port -gt 65535)) {
    throw "Dashboard port must be between 1024 and 65535"
}

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$dashboard = Join-Path $resolvedRepo ".venv\Scripts\helis-dashboard.exe"
if (-not (Test-Path -LiteralPath $dashboard -PathType Leaf)) {
    throw "HELIS dashboard is not installed: $dashboard. Run: .\.venv\Scripts\python.exe -m pip install -e `".[dev]`""
}

Push-Location -LiteralPath $resolvedRepo
try {
    & $dashboard serve --port $Port
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "HELIS dashboard exited with code $exitCode"
    }
} finally {
    Pop-Location
}
