[CmdletBinding()]
param(
    [string] $RepoRoot = (Join-Path $env:USERPROFILE "Helis"),

    [string] $ShortcutName = "HELIS Dashboard",

    [int] $Port = 8765,

    [switch] $Replace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (($Port -lt 1024) -or ($Port -gt 65535)) {
    throw "Dashboard port must be between 1024 and 65535"
}
if ([string]::IsNullOrWhiteSpace($ShortcutName) -or $ShortcutName.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {
    throw "ShortcutName must be a valid Windows file name"
}

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
$launcher = Join-Path $resolvedRepo "deploy\windows\Start-HelisDashboard.ps1"
$dashboard = Join-Path $resolvedRepo ".venv\Scripts\helis-dashboard.exe"
foreach ($required in @($launcher, $dashboard)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required HELIS file does not exist: $required"
    }
    if ($required.Contains('"')) {
        throw "HELIS shortcut paths cannot contain a double quote"
    }
}

$desktop = [Environment]::GetFolderPath([System.Environment+SpecialFolder]::DesktopDirectory)
if ([string]::IsNullOrWhiteSpace($desktop) -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw "Windows desktop directory is unavailable"
}
$shortcutPath = Join-Path $desktop ($ShortcutName + ".lnk")
if ((Test-Path -LiteralPath $shortcutPath) -and (-not $Replace)) {
    throw "Shortcut already exists: $shortcutPath. Re-run with -Replace to update it explicitly."
}

$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -RepoRoot "{1}" -Port {2}' -f `
    $launcher, $resolvedRepo, $Port
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $resolvedRepo
$shortcut.Description = "Open the local read-only HELIS owner dashboard"
$shortcut.WindowStyle = 1
$shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,14"
$shortcut.Save()

if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) {
    throw "Windows did not create the shortcut: $shortcutPath"
}

Write-Host "HELIS dashboard shortcut created: $shortcutPath"
Write-Host "Double-click it to open http://127.0.0.1:$Port"
