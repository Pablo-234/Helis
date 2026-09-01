[CmdletBinding()]
param(
    [string] $EnvFile = (Join-Path $env:USERPROFILE ".config\helis\helis.env")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
$seen = @{}
foreach ($line in Get-Content -LiteralPath $resolvedEnv -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (($trimmed.Length -eq 0) -or $trimmed.StartsWith("#")) {
        continue
    }

    $separator = $line.IndexOf("=")
    if ($separator -le 0) {
        throw "Invalid HELIS environment entry; expected NAME=VALUE"
    }
    $name = $line.Substring(0, $separator).Trim()
    if ($name -notmatch "^HELIS_[A-Z0-9_]+$") {
        throw "Invalid HELIS environment variable name: $name"
    }
    if ($seen.ContainsKey($name)) {
        throw "Duplicate HELIS environment variable: $name"
    }

    $value = $line.Substring($separator + 1).Trim()
    if (($value.Length -ge 2) -and (
        (($value[0] -eq '"') -and ($value[$value.Length - 1] -eq '"')) -or
        (($value[0] -eq "'") -and ($value[$value.Length - 1] -eq "'"))
    )) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    $seen[$name] = $true
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}
