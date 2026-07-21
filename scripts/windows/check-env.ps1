<#
.SYNOPSIS
    Windows ネイティブ開発に必要なツールが揃っているかを確認する。
#>
$ErrorActionPreference = "Stop"

function Test-Tool {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        Name   = $Name
        Status = if ($command) { "OK" } else { "MISSING" }
        Path   = if ($command) { $command.Source } else { "" }
    }
}

Write-Host "[check-env] Required tools"
$required = @("git", "uv", "python", "node", "npm", "make")
$requiredResults = $required | ForEach-Object { Test-Tool $_ }
$requiredResults | Format-Table -AutoSize

Write-Host "[check-env] Optional tools"
$optional = @("gh", "mdbook", "rg", "pwsh")
$optional | ForEach-Object { Test-Tool $_ } | Format-Table -AutoSize

Write-Host "[check-env] Versions"
foreach ($tool in @("git", "uv", "node", "npm")) {
    if (Get-Command $tool -ErrorAction SilentlyContinue) {
        $version = (& $tool --version 2>&1 | Select-Object -First 1)
        Write-Host ("  {0,-8} {1}" -f $tool, $version)
    }
}
if (Test-Path -LiteralPath ".venv/Scripts/python.exe") {
    Write-Host ("  {0,-8} {1}" -f "venv", (& .venv/Scripts/python.exe --version))
} else {
    Write-Host "  venv     (not created; run `uv sync --all-extras`)"
}

Write-Host "[check-env] Git configuration"
$longPaths = (& git config --get core.longpaths)
if ($longPaths -ne "true") {
    # このリポジトリには 141 文字のパスがあり、深い場所では MAX_PATH に当たる。
    Write-Host "  core.longpaths is not enabled. Recommended: git config core.longpaths true"
} else {
    Write-Host "  core.longpaths = true"
}

$missing = @($requiredResults | Where-Object { $_.Status -ne "OK" })
if ($missing.Count -gt 0) {
    Write-Error ("Missing required tools: " + (($missing | ForEach-Object { $_.Name }) -join ", "))
    exit 1
}

Write-Host "[check-env] OK"
