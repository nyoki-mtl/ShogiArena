# 設定システム

ShogiArena の `settings.yaml` は、標準の `output_dir`、`engine_dir`、artifact 用のリポジトリ定義などを管理します。

## 設定ファイルの場所

| プラットフォーム | デフォルトパス |
| --- | --- |
| Linux | `~/.config/shogiarena/settings.yaml` |
| macOS | `~/Library/Application Support/shogiarena/settings.yaml` |
| Windows | `%APPDATA%\\shogiarena\\settings.yaml` |

## 初期化

### 対話モード

```bash
shogiarena config init
```

### 非対話モード

```bash
shogiarena config init --non-interactive \
  --output-dir /data/shogiarena/output \
  --engine-dir /data/shogiarena/engines \
  --github-token-env SHOGIARENA_GITHUB_TOKEN
```

主なオプション:

| オプション | 説明 |
| --- | --- |
| `--non-interactive` | 対話モードを使わない |
| `--output-dir PATH` | 標準の出力ディレクトリ |
| `--engine-dir PATH` | 標準のエンジン格納ディレクトリ |
| `--settings PATH` | `settings.yaml` の出力先 |
| `--github-token-env NAME` | private repo 用トークンを保持する環境変数名（既定 `SHOGIARENA_GITHUB_TOKEN`） |
| `--force` | 既存ファイルを上書き |

## `config init` をしない場合

`settings.yaml` がなくても動作します。
絶対パスで書いた engine YAML と run YAML だけでも実行でき、このときは次のデフォルト値が使われます。

| 項目 | 値 |
| --- | --- |
| `output_dir` | `./shogiarena_output` |
| `engine_dir` | システム一時ディレクトリ配下の `shogiarena-engines` |

プレースホルダーや artifact を使わないなら、初期化を省略しても問題ありません。

## 設定の確認

```bash
shogiarena config show
shogiarena config show --json
```

標準出力の例:

```text
settings_path : /home/user/.config/shogiarena/settings.yaml
output_dir    : /home/user/.local/share/shogiarena/output
engine_dir    : /home/user/.local/share/shogiarena/engines
repos: (none)
overlays: (none)
openbench: (none)
```

## 設定の変更

現在の CLI に `shogiarena config set ...` はありません。
変更方法は次の 2 つです。

- `shogiarena config init --force ...` で再生成する
- `settings.yaml` を直接編集する

run ごとに `output_dir` だけ変えたい場合は、グローバル `--output-dir` を使えます。

```bash
shogiarena --output-dir /tmp/arena-output run tournament tournament.yaml
```

## リポジトリ管理

artifact ベースのエンジンを使う場合はリポジトリ定義が必要です。

### 追加

```bash
shogiarena config repo set yaneuraou \
  --path ~/repos/YaneuraOu \
  --url https://github.com/yaneurao/YaneuraOu.git \
  --build-config ~/.config/shogiarena/builds/yaneuraou.yaml
```

### 削除

```bash
shogiarena config repo remove yaneuraou
```

## プレースホルダー

以下のプレースホルダーは実行時に展開されます。

- `{output_dir}`
- `{engine_dir}`

例:

```yaml
engine_path: "{engine_dir}/yaneuraou/YaneuraOu"
```

```yaml
records_output:
  output_dir: "{output_dir}/records/selfplay"
```

## GitHub トークン

private repository にアクセスする場合は、トークン本体ではなく**トークンを保持する環境変数の名前**を設定します。
`settings.yaml` にトークンそのものを書くことはできません。

```bash
shogiarena config init --github-token-env SHOGIARENA_GITHUB_TOKEN
```

または `settings.yaml` を直接編集します。

```yaml
github_token_env: SHOGIARENA_GITHUB_TOKEN
```

トークン本体は実行時に環境変数から解決されます。

```bash
export SHOGIARENA_GITHUB_TOKEN=ghp_xxxxxxxxxxxx   # Linux / macOS
$env:SHOGIARENA_GITHUB_TOKEN = "ghp_xxxxxxxxxxxx" # Windows PowerShell
```

`github_token_env` を省略した場合の既定値も `SHOGIARENA_GITHUB_TOKEN` です。
トークンは秘密情報なので、リポジトリやログに含めないでください。

## OpenBench / ShogiBench

SPRT 実行時に OpenBench / ShogiBench へ送信したい場合は `settings.yaml` 側にも既定値を置けます。

```yaml
openbench:
  server: https://your-openbench.example.com
  username: your-user
  password_env: OPENBENCH_PASSWORD
```

パスワード本体は保存せず、環境変数から読み込みます。

```bash
export OPENBENCH_PASSWORD='your-password'
```

run 設定側に `openbench:` を書いた場合は、そちらが優先されます。

## 複数の設定を使い分ける

設定ファイルパスを切り替える専用のランタイムオプションは現在ありません。
通常は 1 つの `settings.yaml` を使い、run ごとの差分は `--output-dir` や個別 YAML 側で吸収します。

## 関連ドキュメント

- [クイックスタート](../getting-started/quick-start.md)
- [エンジン設定ファイル](engine-configuration.md)
