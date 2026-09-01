[CmdletBinding()]
param(
    [string] $RepoRoot = (Join-Path $env:USERPROFILE "Helis"),

    [string] $EnvFile = (Join-Path $env:USERPROFILE ".config\helis\helis.env"),

    [switch] $Replace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Quote-TaskArgument {
    param([Parameter(Mandatory = $true)][string] $Value)

    if ($Value.Contains('"')) {
        throw "Task arguments cannot contain a double quote"
    }
    return '"' + $Value + '"'
}

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
$wakeScript = Join-Path $resolvedRepo "deploy\windows\Invoke-HelisWake.ps1"
if (-not (Test-Path -LiteralPath $wakeScript -PathType Leaf)) {
    throw "HELIS wake script does not exist: $wakeScript"
}

$taskSpecs = @(
    @{
        Name = "HELIS Discovery"
        Mode = "Discovery"
        IntervalMinutes = 15
        ExecutionMinutes = 15
        Description = "Bounded HELIS market discovery wake"
    },
    @{
        Name = "HELIS Scheduler"
        Mode = "Scheduler"
        IntervalMinutes = 5
        ExecutionMinutes = 10
        Description = "Bounded HELIS portfolio scheduler wake"
    }
)

$existing = @(
    $taskSpecs | Where-Object {
        $null -ne (Get-ScheduledTask -TaskName $_.Name -ErrorAction SilentlyContinue)
    }
)
if (($existing.Count -gt 0) -and (-not $Replace)) {
    $names = ($existing | ForEach-Object { $_.Name }) -join ", "
    throw "Scheduled task already exists: $names. Re-run with -Replace to update it explicitly."
}

$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
    -RunLevel Limited

foreach ($spec in $taskSpecs) {
    $arguments = @(
        "-NoProfile",
        "-NonInteractive",
        "-File", (Quote-TaskArgument -Value $wakeScript),
        "-Mode", $spec.Mode,
        "-RepoRoot", (Quote-TaskArgument -Value $resolvedRepo),
        "-EnvFile", (Quote-TaskArgument -Value $resolvedEnv)
    ) -join " "
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $spec.IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $spec.ExecutionMinutes)

    $registration = @{
        TaskName = $spec.Name
        Action = $action
        Trigger = $trigger
        Settings = $settings
        Principal = $principal
        Description = $spec.Description
    }
    if ($Replace) {
        $registration["Force"] = $true
    }
    Register-ScheduledTask @registration | Out-Null
    Write-Host "Registered $($spec.Name) for $userId"
}

Write-Host "HELIS tasks run only while this user is logged on and keep secrets in $resolvedEnv."
