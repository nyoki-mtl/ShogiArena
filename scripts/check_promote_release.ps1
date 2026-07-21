<#
.SYNOPSIS
    promote_release_main.ps1 が正しい release commit を作ることを検証する。

.DESCRIPTION
    一時ディレクトリに develop の bare リポジトリと、分岐した dev / main を作り、
    promote スクリプトを実行して次を確認する。

    - subject が `Release <version>` である
    - 親が 1 つで、それが元の main である（履歴が繋がっている）
    - tree が source ref（dev）の tree と完全に一致する

    実リポジトリには一切触れず、push もしない。

    このチェックは「PowerShell が git のフラグを飲み込む」種類の事故を捕まえる。
    実際に `commit-tree -p HEAD` の `-p` が共通パラメータ `-PipelineVariable` に
    前方一致して値ごと消え、親なし commit ができるバグを検出した実績がある。
#>
$ErrorActionPreference = "Stop"

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & git @GitArgs; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) { throw "git $($GitArgs -join ' ') failed with exit code $code" }
}

function Get-GitOutput {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & git @GitArgs; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    if ($code -ne 0) { throw "git $($GitArgs -join ' ') failed with exit code $code" }
    return $out
}

function Set-GitIdentity {
    Invoke-Git config user.name "promote-check"
    Invoke-Git config user.email "promote-check@example.com"
}

function Write-TestFile {
    param([string]$Path, [string]$Content = "x")
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Set-Content -LiteralPath $Path -Value $Content -Encoding utf8
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$promoteScript = Join-Path $repoRoot "scripts/promote_release_main.ps1"
if (-not (Test-Path -LiteralPath $promoteScript)) {
    throw "promote script not found: $promoteScript"
}

$tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("shogiarena-promote-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmpRoot -Force | Out-Null
$originalLocation = Get-Location

try {
    $developBare = Join-Path $tmpRoot "develop.git"
    Invoke-Git init -q --bare --initial-branch=main $developBare

    # --- dev と main が分岐した履歴を作る ---------------------------------
    $seed = Join-Path $tmpRoot "seed"
    Invoke-Git init -q --initial-branch=main $seed
    Set-Location $seed
    Set-GitIdentity

    Write-TestFile (Join-Path $seed "README.md") "base"
    Invoke-Git add -A
    Invoke-Git commit -q -m "base"
    Invoke-Git remote add develop $developBare
    Invoke-Git push -q develop main

    # main 側にだけ進んだ過去の release commit
    Write-TestFile (Join-Path $seed "README.md") "main-only"
    Invoke-Git commit -q -a -m "Release v0.9.0"
    Invoke-Git push -q develop main

    # dev 側は別方向へ進む
    Invoke-Git checkout -q -b dev HEAD~1
    Write-TestFile (Join-Path $seed "README.md") "dev"
    Write-TestFile (Join-Path $seed "src/new_feature.py") "print('hi')"
    Invoke-Git add -A
    Invoke-Git commit -q -m "feat: new feature"
    Invoke-Git push -q develop dev

    $devTree = (Get-GitOutput rev-parse "dev^{tree}")

    # --- promote を実行 ----------------------------------------------------
    $work = Join-Path $tmpRoot "work"
    Invoke-Git clone -q $developBare $work
    Set-Location $work
    Set-GitIdentity
    Invoke-Git remote rename origin develop
    Invoke-Git fetch -q develop

    $previousMain = (Get-GitOutput rev-parse "develop/main")

    $powerShellExe = (Get-Process -Id $PID).Path
    $powerShellArgs = @("-NoProfile")
    if ([System.IO.Path]::GetFileName($powerShellExe) -ieq "powershell.exe") {
        $powerShellArgs += @("-ExecutionPolicy", "Bypass")
    }
    $powerShellArgs += @("-File", $promoteScript, "v1.0.0", "develop/dev", "main", "develop/main")

    & $powerShellExe @powerShellArgs
    if ($LASTEXITCODE -ne 0) {
        throw "promote_release_main.ps1 failed with exit code $LASTEXITCODE"
    }

    # --- 検証 ---------------------------------------------------------------
    $subject = (Get-GitOutput log -1 --pretty=%s main)
    $parents = @((Get-GitOutput log -1 --pretty=%P main) -split '\s+' | Where-Object { $_ })
    $newTree = (Get-GitOutput rev-parse "main^{tree}")
    $files = @(Get-GitOutput ls-tree -r --name-only main)

    if ($subject -ne "Release v1.0.0") {
        throw "unexpected commit subject: $subject"
    }
    if ($parents.Count -ne 1) {
        throw "expected exactly 1 parent, got $($parents.Count) (history would be detached)"
    }
    if ($parents[0] -ne $previousMain) {
        throw "parent is not the previous main commit"
    }
    if ($newTree -ne $devTree) {
        throw "main tree does not match develop/dev tree"
    }
    if ($files -notcontains "src/new_feature.py") {
        throw "dev content is missing from the release commit"
    }

    Write-Host "promote release check OK"
} finally {
    Set-Location $originalLocation
    Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
}
