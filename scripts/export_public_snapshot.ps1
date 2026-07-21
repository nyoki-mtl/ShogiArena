<#
.SYNOPSIS
    develop リポジトリから public リポジトリ向けのスナップショットコミットを作る。

.DESCRIPTION
    export-public ブランチを public/main（未作成なら orphan）から作り直し、
    source ref の内容を dev-only ファイルを除外して重ねる。

    1) develop / public remote を fetch
    2) export branch を public base から作成（無ければ orphan で作り直し）
    3) source ref を dev-only パス除外で overlay
    4) 除外パスをスナップショットから削除
    5) source ref に無いファイルを削除して差分を反映
    6) release snapshot commit を作成（任意で push / tag）

    実行前に working tree / index / untracked files が空である必要がある。

.EXAMPLE
    scripts\export_public_snapshot.ps1 v0.8.0

.EXAMPLE
    scripts\export_public_snapshot.ps1 -NotesFile release-notes\v0.8.0.txt -Push -Tag v0.8.0

.EXAMPLE
    scripts\export_public_snapshot.ps1 -DryRun v0.8.0
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [string]$Version,

    [Parameter(Position = 1)]
    [string]$SourceRef = "develop/main",

    [Parameter(Position = 2)]
    [string]$PublicBase = "",

    [switch]$DryRun,

    [switch]$Push,

    [switch]$Tag,

    [string]$NotesFile = "",

    [string]$TagMessage = "",

    [string]$PublicRemote = "public",

    [string]$PublicBranch = "main"
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

function Build-CommitMessage {
    param(
        [string]$ReleaseVersion,
        [string[]]$ReleaseNotesLines
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("Release $ReleaseVersion")
    if ($ReleaseNotesLines) {
        $lines.Add("")
        foreach ($line in $ReleaseNotesLines) {
            $lines.Add($line)
        }
    }
    return ($lines -join "`n") + "`n"
}

# 秘密が入りうるファイルは、リポジトリ直下だけでなく全階層で拒否する。
# frontend/.env.production のようなネストしたファイルは .gitignore の `.env` にも
# 掛からないため、ここが最後の防衛線になる。
function Test-SecretLikePath {
    param([string]$Path)

    $leaf = Split-Path -Path $Path -Leaf
    return (
        $leaf -eq ".env" -or
        $leaf -like ".env.*" -or
        $leaf -eq ".envrc" -or
        $leaf -eq ".npmrc" -or
        $leaf -eq ".pypirc" -or
        $leaf -like "*.pem" -or
        $leaf -like "*.key" -or
        $leaf -like "*.p12" -or
        $leaf -like "*.pfx" -or
        $leaf -like "id_rsa*" -or
        $leaf -like "id_ecdsa*" -or
        $leaf -like "id_ed25519*" -or
        $Path -eq ".secrets" -or
        $Path -like ".secrets/*" -or
        $Path -like "*/.secrets/*"
    )
}

function Assert-NoDevOnlyPaths {
    # commit 前に検査する。commit 後だと不正なスナップショットが履歴に残ってしまう。
    $found = $false
    foreach ($path in (Get-GitOutput ls-files)) {
        if (Test-SecretLikePath $path) {
            Write-Error "secret-like path would be exported: $path"
            $found = $true
            continue
        }
        if (
            $path -eq "AGENTS.md" -or
            $path -eq "CLAUDE.md" -or
            $path -eq "GEMINI.md" -or
            $path -like ".agents/*" -or
            $path -like ".cursor/*" -or
            $path -like ".codex/*" -or
            $path -like ".claude/*" -or
            $path -like ".vscode/*" -or
            $path -like "agent-docs/*" -or
            $path -like "release-notes/*" -or
            $path -like "_refs/*" -or
            $path -like ".sandbox/*" -or
            $path -like ".serena/*" -or
            $path -like "dist/*" -or
            $path -like "site/*" -or
            $path -eq ".github/workflows/develop-ci.yml"
        ) {
            Write-Error "dev-only path would be exported: $path"
            $found = $true
        }
    }

    if ($found) {
        throw "public snapshot verification failed"
    }
}

function Remove-PathIfExists {
    param([string]$Path)

    # ワイルドカードを含むパターン（.env.* など）は -LiteralPath では展開されないため、
    # 一致した実体を列挙して消す。展開しないと除去が黙って no-op になる。
    if ($Path.Contains("*")) {
        foreach ($item in (Get-ChildItem -Path $Path -Force -ErrorAction SilentlyContinue)) {
            Remove-Item -LiteralPath $item.FullName -Recurse -Force
        }
        return
    }

    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

if (-not $PublicBase) {
    $PublicBase = "$PublicRemote/$PublicBranch"
}

if ($NotesFile -and -not (Test-Path -LiteralPath $NotesFile)) {
    throw "notes file が見つかりません: $NotesFile"
}

# release-notes/ は除外対象なので、overlay と削除の過程で作業ツリーから消える。
# コミット直前に読もうとすると必ず失敗するため、ここで内容を確保しておく。
$releaseNotesLines = if ($NotesFile) { @(Get-Content -LiteralPath $NotesFile) } else { @() }

$exportBranch = "export-public"
$publicGitAuthorName = if ($env:PUBLIC_GIT_AUTHOR_NAME) { $env:PUBLIC_GIT_AUTHOR_NAME } else { "nyoki-mtl" }
$publicGitAuthorEmail = if ($env:PUBLIC_GIT_AUTHOR_EMAIL) { $env:PUBLIC_GIT_AUTHOR_EMAIL } else { "charmer.popopo@gmail.com" }

$excludePaths = @(
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".agents/**",
    ".cursor/**",
    ".codex/**",
    ".claude/**",
    ".vscode/**",
    # 秘密が入りうるパターンは全階層で除外する（pathspec の * は / にも一致する）。
    ".env",
    "*/.env",
    ".env.*",
    "*/.env.*",
    "*.envrc",
    "*.npmrc",
    "*.pypirc",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "*/id_rsa*",
    "id_ecdsa*",
    "*/id_ecdsa*",
    "id_ed25519*",
    "*/id_ed25519*",
    ".secrets/**",
    "*/.secrets/**",
    "agent-docs/**",
    "release-notes/**",
    "_refs/**",
    ".sandbox/**",
    ".serena/**",
    "dist/**",
    "site/**",
    ".github/workflows/develop-ci.yml"
)

# removePaths は SourceRef に commit されているパスだけを列挙する。
# untracked なローカル専用ディレクトリは checkout から除外されるだけで、
# ディスクから消す必要はない。
$removePaths = @(
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".agents",
    ".cursor",
    ".codex",
    ".claude",
    ".vscode",
    ".env",
    ".env.*",
    ".secrets",
    "agent-docs",
    "release-notes",
    "dist",
    "site",
    ".github/workflows/develop-ci.yml"
)

Require-CleanTree

Write-Host "[1/5] Fetch remotes"
Invoke-Git fetch develop
Invoke-Git fetch $PublicRemote

if (-not (Test-GitSuccess rev-parse --verify --quiet $SourceRef)) {
    throw "source ref が見つかりません: $SourceRef"
}

$hasPublicBase = Test-GitSuccess rev-parse --verify --quiet $PublicBase

Write-Host "[2/5] Prepare export branch"
if ($hasPublicBase) {
    Write-Host "  base: $PublicBase"
    Invoke-Git checkout -B $exportBranch $PublicBase
} else {
    Write-Host "  base: (none, recreating orphan branch)"

    if (Test-GitSuccess rev-parse --verify --quiet "refs/heads/$exportBranch") {
        if ((Get-GitOutput branch --show-current) -eq $exportBranch) {
            Invoke-Git checkout --detach HEAD
        }
        # -D はクォートしないと PowerShell が共通パラメータ -Debug として束縛してしまう。
        Invoke-Git branch "-D" $exportBranch
    }

    Invoke-Git checkout --orphan $exportBranch
    & git rm -r -f --ignore-unmatch . *> $null
}

Write-Host "[3/5] Overlay $SourceRef with excluded dev-only paths"
$checkoutArgs = @($SourceRef, "--", ".")
foreach ($path in $excludePaths) {
    $checkoutArgs += ":(exclude)$path"
}
Invoke-Git checkout @checkoutArgs

Write-Host "[4/5] Remove excluded paths from public snapshot"
& git rm -r --ignore-unmatch -- @removePaths *> $null
foreach ($path in $removePaths) {
    Remove-PathIfExists $path
}

Write-Host "[5/5] Reflect deletions from $SourceRef"
if ($hasPublicBase) {
    foreach ($path in (Get-GitOutput diff --name-only --diff-filter=D "$PublicBase..$SourceRef")) {
        if ($path) {
            & git rm --ignore-unmatch -- $path *> $null
        }
    }
}

# SourceRef に存在しないファイルを一掃する（差分ベースより厳密な集合差）。
$sourcePaths = [System.Collections.Generic.HashSet[string]]::new(
    [string[]](Get-GitOutput ls-tree -r --name-only $SourceRef),
    [System.StringComparer]::Ordinal
)
foreach ($path in (Get-GitOutput ls-files)) {
    if ($path -and -not $sourcePaths.Contains($path)) {
        & git rm --ignore-unmatch -- $path *> $null
    }
}

Invoke-Git add -A
Assert-NoDevOnlyPaths

if (Test-GitSuccess diff --cached --quiet) {
    Write-Host "No changes to export."
    exit 0
}

if ($DryRun) {
    Write-Host "Dry run OK. Snapshot is staged and no commit was created."
    Invoke-Git diff --cached --stat
    exit 0
}

$commitMessageFile = [System.IO.Path]::GetTempFileName()
try {
    $message = Build-CommitMessage $Version $releaseNotesLines
    # git は UTF-8 BOM をメッセージ本文に含めてしまうため BOM なしで書く。
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($commitMessageFile, $message, $utf8NoBom)

    $env:GIT_AUTHOR_NAME = $publicGitAuthorName
    $env:GIT_AUTHOR_EMAIL = $publicGitAuthorEmail
    $env:GIT_COMMITTER_NAME = $publicGitAuthorName
    $env:GIT_COMMITTER_EMAIL = $publicGitAuthorEmail
    Invoke-Git commit -F $commitMessageFile
} finally {
    Remove-Item -LiteralPath $commitMessageFile -Force -ErrorAction SilentlyContinue
}

if ($Tag) {
    if (Test-GitSuccess rev-parse --verify --quiet "refs/tags/$Version") {
        throw "tag already exists locally: $Version"
    }
    if (Test-GitSuccess ls-remote --exit-code --tags $PublicRemote "refs/tags/$Version") {
        throw "tag already exists on ${PublicRemote}: $Version"
    }
    $resolvedTagMessage = if ($TagMessage) { $TagMessage } else { "Release version $($Version -replace '^v', '')" }
    Invoke-Git tag -a $Version -m $resolvedTagMessage
}

if ($Push) {
    Invoke-Git push $PublicRemote "HEAD:$PublicBranch"
    if ($Tag) {
        Invoke-Git push $PublicRemote $Version
    }
    Write-Host "Snapshot commit created and pushed on branch '$exportBranch'."
    Write-Host "Pushed:"
    Write-Host "  $PublicRemote/$PublicBranch"
    if ($Tag) {
        Write-Host "  $PublicRemote tag $Version"
    }
} else {
    Write-Host "Snapshot commit created on branch '$exportBranch'."
    Write-Host "Next:"
    Write-Host "  git push $PublicRemote HEAD:$PublicBranch"
}
