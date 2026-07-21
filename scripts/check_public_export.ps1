<#
.SYNOPSIS
    export_public_snapshot.ps1 が dev-only ファイルを確実に除外することを検証する。

.DESCRIPTION
    一時ディレクトリに develop / public の bare リポジトリと作業クローンを作り、
    dev-only パスを 1 つずつ含んだソースを push してから export スクリプトを
    -DryRun で実行し、staged なスナップショットに dev-only パスが残らないこと、
    public base の陳腐化ファイルが消えることを確認する。

    実リポジトリには一切触れない。
#>
$ErrorActionPreference = "Stop"

function Invoke-Git {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$GitArgs
    )

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

function Set-GitIdentity {
    Invoke-Git config user.name "export-check"
    Invoke-Git config user.email "export-check@example.com"
}

function Write-TestFile {
    param([string]$Path, [string]$Content = "x")

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Set-Content -LiteralPath $Path -Value $Content -Encoding utf8
}

function Assert-NotStaged {
    param([string[]]$StagedPaths, [string]$Path)

    if ($StagedPaths -contains $Path) {
        throw "dev-only path leaked into public snapshot: $Path"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$exportScript = Join-Path $repoRoot "scripts/export_public_snapshot.ps1"
if (-not (Test-Path -LiteralPath $exportScript)) {
    throw "export script not found: $exportScript"
}

# dev-only パス（export されてはいけない）。export_public_snapshot.ps1 の
# excludePaths / Assert-NoDevOnlyPaths と対応させる。
$devOnlyFiles = @(
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".agents/private.md",
    ".cursor/rules.md",
    ".codex/private.md",
    ".claude/settings.json",
    ".vscode/settings.json",
    ".env",
    ".env.production",
    ".secrets/token.txt",
    # ネストした秘密ファイル。.gitignore の `.env` にも掛からないため、
    # export 側の全階層パターンが唯一の防衛線になる。
    "src/shogiarena/_core/interfaces/dashboard/frontend/.env.production",
    "packages/worker/.env",
    "deploy/.secrets/token.txt",
    "deploy/server.pem",
    "deploy/tls.key",
    "deploy/ssh/id_rsa",
    "tools/.npmrc",
    "agent-docs/tasks/private.md",
    "release-notes/v0.9.0.txt",
    "_refs/vendored/note.md",
    ".sandbox/private.txt",
    ".serena/private.txt",
    "dist/artifact.whl",
    "site/index.html",
    ".github/workflows/develop-ci.yml"
)

$publicFiles = @(
    "README.md",
    "pyproject.toml",
    "src/shogiarena/__init__.py",
    ".github/workflows/public-ci.yml"
)

$tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("shogiarena-public-export-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmpRoot -Force | Out-Null

$originalLocation = Get-Location
try {
    $developBare = Join-Path $tmpRoot "develop.git"
    $publicBare = Join-Path $tmpRoot "public.git"
    Invoke-Git init -q --bare --initial-branch=main $developBare
    Invoke-Git init -q --bare --initial-branch=main $publicBare

    # --- source (develop/main) を作る --------------------------------------
    $sourceRepo = Join-Path $tmpRoot "source"
    Invoke-Git init -q --initial-branch=main $sourceRepo
    Set-Location $sourceRepo
    Set-GitIdentity
    foreach ($path in ($publicFiles + $devOnlyFiles)) {
        Write-TestFile (Join-Path $sourceRepo $path)
    }
    # -f を付けないと、利用者の global core.excludesFile が .env などを弾いて
    # seed が tracked にならず、検証が「何も無いので合格」に化ける。
    Invoke-Git add -A -f
    Invoke-Git commit -q -m "seed source"
    $trackedSeed = [System.Collections.Generic.HashSet[string]]::new(
        [string[]](Get-GitOutput ls-files),
        [System.StringComparer]::Ordinal
    )
    foreach ($path in ($publicFiles + $devOnlyFiles)) {
        if (-not $trackedSeed.Contains($path)) {
            throw "seed file was not tracked, verification would be vacuous: $path"
        }
    }
    Invoke-Git remote add develop $developBare
    Invoke-Git push -q develop main

    # --- 陳腐化した public base を作る -------------------------------------
    $publicWork = Join-Path $tmpRoot "public-work"
    Invoke-Git init -q --initial-branch=main $publicWork
    Set-Location $publicWork
    Set-GitIdentity
    Write-TestFile (Join-Path $publicWork "README.md") "old"
    Write-TestFile (Join-Path $publicWork "obsolete.txt") "should disappear"
    # public base に紛れ込んだ dev-only ファイルも掃除されること。
    Write-TestFile (Join-Path $publicWork "AGENTS.md") "stray"
    Invoke-Git add -A
    Invoke-Git commit -q -m "seed public base"
    Invoke-Git remote add public $publicBare
    Invoke-Git push -q public main

    # --- export を -DryRun で実行 ------------------------------------------
    $exportWork = Join-Path $tmpRoot "export-work"
    Invoke-Git clone -q $publicBare $exportWork
    Set-Location $exportWork
    Set-GitIdentity
    Invoke-Git remote rename origin public
    Invoke-Git remote add develop $developBare
    Invoke-Git fetch -q develop
    Invoke-Git fetch -q public

    $powerShellExe = (Get-Process -Id $PID).Path
    $powerShellArgs = @("-NoProfile")
    if ([System.IO.Path]::GetFileName($powerShellExe) -ieq "powershell.exe") {
        $powerShellArgs += @("-ExecutionPolicy", "Bypass")
    }
    # スクリプトは実リポジトリのものを直接呼ぶ（cwd が export-work なので対象は一時リポジトリ）。
    # 一時リポジトリに置くと staged/untracked になり Require-CleanTree に引っかかる。
    $powerShellArgs += @("-File", $exportScript, "-DryRun", "v0.0.0", "develop/main", "public/main")

    & $powerShellExe @powerShellArgs
    if ($LASTEXITCODE -ne 0) {
        throw "export_public_snapshot.ps1 failed with exit code $LASTEXITCODE"
    }

    # --- release notes 経路の検証 ------------------------------------------
    # release-notes/ は除外対象なので overlay 中に作業ツリーから消える。
    # コミットメッセージ生成がそれより後に読みにいくと必ず失敗する。
    # 実運用では main（全ファイルを含む tree）から相対パスで渡すので、その形を再現する。
    # DryRun は index に staged なスナップショットを残すので、完全に戻してから実行する。
    Invoke-Git reset -q --hard HEAD
    Invoke-Git clean -qfd
    Invoke-Git checkout -q -B main develop/main
    Invoke-Git clean -qfd
    if (-not (Test-Path -LiteralPath "release-notes/v0.9.0.txt")) {
        throw "notes file must exist in the working tree for this check to be meaningful"
    }
    $notesArgs = @("-NoProfile")
    if ([System.IO.Path]::GetFileName($powerShellExe) -ieq "powershell.exe") {
        $notesArgs += @("-ExecutionPolicy", "Bypass")
    }
    $notesArgs += @(
        "-File", $exportScript,
        "-NotesFile", "release-notes/v0.9.0.txt",
        "v0.0.0", "develop/main", "public/main"
    )
    & $powerShellExe @notesArgs
    if ($LASTEXITCODE -ne 0) {
        throw "export_public_snapshot.ps1 with -NotesFile failed with exit code $LASTEXITCODE"
    }
    $commitSubject = (Get-GitOutput log -1 --format=%s)
    if ($commitSubject -ne "Release v0.0.0") {
        throw "unexpected release commit subject: $commitSubject"
    }
    $commitBody = (Get-GitOutput log -1 --format=%b) -join "`n"
    if ($commitBody -notmatch "x") {
        throw "release notes were not embedded in the export commit message"
    }
    $snapshotPaths = @(Get-GitOutput ls-files)

    # --- 検証 ---------------------------------------------------------------
    # -DryRun では index がそのままスナップショットの中身になる。
    # `diff --cached` は削除も列挙してしまうので、index の内容だけを見る。
    $snapshotPaths = @(Get-GitOutput ls-files)

    foreach ($path in $devOnlyFiles) {
        Assert-NotStaged $snapshotPaths $path
    }
    if ($snapshotPaths -contains "obsolete.txt") {
        throw "obsolete public file was not removed: obsolete.txt"
    }
    foreach ($path in $publicFiles) {
        if ($snapshotPaths -notcontains $path) {
            throw "expected public file missing from snapshot: $path"
        }
    }

    Write-Host "public export check OK"
} finally {
    Set-Location $originalLocation
    Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
}
