<#
.SYNOPSIS
    source ref のツリーと一致する単一親のリリースコミットを target branch 上に作る。

.DESCRIPTION
    dev と main のリリース履歴が分岐しているときに squash-merge の衝突を避けつつ、
    dev -> main の一方向リリースフローを保つ。

.EXAMPLE
    scripts\promote_release_main.ps1 v0.8.0

.EXAMPLE
    scripts\promote_release_main.ps1 -Push v0.8.0 develop/dev main develop/main
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [string]$Version,

    [Parameter(Position = 1)]
    [string]$SourceRef = "develop/dev",

    [Parameter(Position = 2)]
    [string]$TargetBranch = "main",

    [Parameter(Position = 3)]
    [string]$TargetBase = "develop/main",

    [switch]$Push
)

$ErrorActionPreference = "Stop"

function Invoke-Git {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GitArgs
    )

    # git は進捗を stderr に出すため、一時的に Continue にしないと終端エラーになる。
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git @GitArgs
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($exitCode -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit code $exitCode"
    }
}

function Test-GitSuccess {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GitArgs
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git @GitArgs *> $null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    return $exitCode -eq 0
}

function Get-GitOutput {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GitArgs
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & git @GitArgs
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($exitCode -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit code $exitCode"
    }
    return $output
}

function Require-CleanTree {
    if (-not (Test-GitSuccess diff --quiet --ignore-submodules --)) {
        throw "working tree に未保存の変更があります"
    }
    if (-not (Test-GitSuccess diff --cached --quiet --ignore-submodules --)) {
        throw "index に staged 変更があります"
    }
    if (Get-GitOutput ls-files --others --exclude-standard) {
        throw "untracked files があります"
    }
}

Require-CleanTree

Invoke-Git fetch develop

if (-not (Test-GitSuccess rev-parse --verify --quiet $SourceRef)) {
    throw "source ref が見つかりません: $SourceRef"
}
if (-not (Test-GitSuccess rev-parse --verify --quiet $TargetBase)) {
    throw "target base が見つかりません: $TargetBase"
}

Invoke-Git checkout -B $TargetBranch $TargetBase

# -p はクォートしないと PowerShell 側で解釈されて git に渡らず、
# 親なし（root commit）になってしまう。-m も同様に明示的に文字列として渡す。
$newCommit = (Get-GitOutput commit-tree "$SourceRef^{tree}" "-p" HEAD "-m" "Release $Version") | Select-Object -First 1

Invoke-Git update-ref "refs/heads/$TargetBranch" $newCommit
Invoke-Git read-tree --reset -u HEAD

if (-not (Test-GitSuccess diff --quiet $TargetBranch $SourceRef)) {
    throw "$TargetBranch tree does not match $SourceRef"
}

if ($Push) {
    Invoke-Git push develop $TargetBranch
}

Write-Host "Release commit created on '$TargetBranch': $newCommit"
Write-Host "Tree source: $SourceRef"
Write-Host "Next:"
Write-Host "  git push develop $TargetBranch"
