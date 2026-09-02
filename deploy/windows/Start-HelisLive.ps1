[CmdletBinding()]
param(
    [string] $RepoRoot = (Join-Path $env:USERPROFILE "Helis"),

    [string] $EnvFile = (Join-Path $env:USERPROFILE ".config\helis\helis.env"),

    [switch] $ConfirmLiveOperations,

    [switch] $ReplaceTasks
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ConfirmLiveOperations) {
    throw "Re-run with -ConfirmLiveOperations to enable bounded recurring market reads, local-model calls and execution of separately approved live actions."
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
$registration = Join-Path $resolvedRepo "deploy\windows\Register-HelisTasks.ps1"
$live = Join-Path $resolvedRepo ".venv\Scripts\helis-live.exe"
$discovery = Join-Path $resolvedRepo ".venv\Scripts\helis-discovery.exe"
$scheduler = Join-Path $resolvedRepo ".venv\Scripts\helis-scheduler.exe"
$operator = Join-Path $resolvedRepo ".venv\Scripts\helis-operator.exe"
foreach ($required in @($envLoader, $registration, $live, $discovery, $scheduler, $operator)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required HELIS file does not exist: $required"
    }
}

$taskNames = @("HELIS Discovery", "HELIS Scheduler")
$registeredForActivation = $false
Push-Location -LiteralPath $resolvedRepo
try {
    & $envLoader -EnvFile $resolvedEnv
    Invoke-CheckedExecutable -Executable $live -Arguments @("bootstrap")
    Invoke-CheckedExecutable -Executable $live -Arguments @("model-status")
    Invoke-CheckedExecutable -Executable $live -Arguments @("model-smoke")
    Invoke-CheckedExecutable -Executable $live -Arguments @("pilot", "--skip-model-probe")
    Invoke-CheckedExecutable -Executable $live -Arguments @("pilot-status")
    Invoke-CheckedExecutable -Executable $live -Arguments @("activation-check", "--no-probe-model")
    Invoke-CheckedExecutable -Executable $discovery -Arguments @("health")
    Invoke-CheckedExecutable -Executable $scheduler -Arguments @("health")

    if ($ReplaceTasks) {
        foreach ($taskName in $taskNames) {
            Disable-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
        }
    }
    $registrationArguments = @(
        "-RepoRoot", $resolvedRepo,
        "-EnvFile", $resolvedEnv,
        "-Disabled"
    )
    if ($ReplaceTasks) {
        $registrationArguments += "-Replace"
    }
    & $registration @registrationArguments
    $registeredForActivation = $true

    Invoke-CheckedExecutable -Executable $live -Arguments @(
        "activation-check",
        "--no-probe-model",
        "--require-schedule"
    )
    foreach ($taskName in $taskNames) {
        Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
    }
    $registeredForActivation = $false
    Invoke-CheckedExecutable -Executable $operator -Arguments @("inbox")
} catch {
    if ($registeredForActivation) {
        foreach ($taskName in $taskNames) {
            Disable-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
        }
    }
    throw
} finally {
    Pop-Location
}

Write-Host "HELIS live operation is enabled. The first bounded wakes are scheduled within one minute."
Write-Host "No publication, first contact or checkout was approved by this launcher."
Write-Host 'Pause both loops with: Disable-ScheduledTask -TaskName "HELIS Discovery", "HELIS Scheduler"'
