[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Discovery", "Scheduler")]
    [string] $Mode,

    [string] $RepoRoot = (Join-Path $env:USERPROFILE "Helis"),

    [string] $EnvFile = (Join-Path $env:USERPROFILE ".config\helis\helis.env")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
$envLoader = Join-Path $resolvedRepo "deploy\windows\Import-HelisEnv.ps1"
if (-not (Test-Path -LiteralPath $envLoader -PathType Leaf)) {
    throw "HELIS environment loader does not exist: $envLoader"
}
$stateRoot = Join-Path $resolvedRepo ".helis"
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
& $envLoader -EnvFile $resolvedEnv

if ($Mode -eq "Discovery") {
    $executable = Join-Path $resolvedRepo ".venv\Scripts\helis-discovery.exe"
    $commandArguments = @(
        "wake",
        "--config", (Join-Path $resolvedRepo "helis.toml"),
        "--db", (Join-Path $resolvedRepo "helis.db"),
        "--minimum-interval-seconds", "3600",
        "--lease-seconds", "900",
        "--observation-limit", "100",
        "--candidate-limit", "5",
        "--max-model-calls", "8",
        "--max-tokens", "40000",
        "--max-cost-cents", "25"
    )
    $logPath = Join-Path $stateRoot "discovery.log"
} else {
    $executable = Join-Path $resolvedRepo ".venv\Scripts\helis-scheduler.exe"
    $commandArguments = @(
        "wake",
        "--db", (Join-Path $resolvedRepo "helis.db"),
        "--workspace-root", (Join-Path $resolvedRepo ".helis\workspaces"),
        "--minimum-interval-seconds", "900",
        "--lease-seconds", "600",
        "--max-advances", "2"
    )
    $logPath = Join-Path $stateRoot "scheduler.log"
}

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "HELIS entry point does not exist: $executable"
}

Push-Location -LiteralPath $resolvedRepo
try {
    & $executable @commandArguments *>> $logPath
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($exitCode -ne 0) {
    throw "HELIS $Mode wake failed with exit code $exitCode; inspect $logPath"
}
