# 設定システム

ShogiArena の設定システムは、出力ディレクトリ、エンジンキャッシュ、リポジトリ管理などの環境設定を一元管理します。

## 設定ファイルの場所

設定は `settings.yaml` に保存されます。場所はプラットフォームによって異なります。

| プラットフォーム | デフォルトパス |
| --- | --- |
| **Linux** | `~/.config/shogiarena/settings.yaml` |
| **macOS** | `~/Library/Application Support/shogiarena/settings.yaml` |
| **Windows** | `%APPDATA%\shogiarena\settings.yaml` |

## 初期化コマンド

### 対話的な初期化

初めて ShogiArena を使用する場合、以下のコマンドで対話的に設定を行います。

```bash
shogiarena init
```

このコマンドは以下を対話的に設定します：

1. **出力ディレクトリ**: トーナメント結果やログの保存先
2. **エンジンディレクトリ**: エンジンバイナリのキャッシュ先
3. **リポジトリ登録**: YaneuraOu などのエンジンリポジトリ（オプション）
4. **FukauraOu (DLShogi互換)**: YaneuraOu と同一repoを使うビルド設定（オプション）
5. **GitHub トークン**: プライベートリポジトリへのアクセス用（オプション）
6. **OpenBench/ShogiBench 接続情報**: SPRT 結果提出先（オプション）

> **Note: FukauraOu の追加条件**
>
> FukauraOu は YaneuraOu のビルドオプション違いのため、`init` では **YaneuraOu を追加した後にのみ**選択できます。
> CUDA を検出できた場合は `nvcc -V` を使ってパスを提示し、確認のうえ `builds/fukauraou.yaml` に反映します。


### 非対話的な初期化

CI や自動化スクリプトでは、コマンドライン引数で全てを指定できます。

```bash
shogiarena config init --non-interactive \
  --output-dir /data/shogiarena/output \
  --engine-dir /data/shogiarena/engines \
  --github-token ghp_xxxxxxxxxxxx
```

#### オプション一覧

| オプション | 説明 |
| --- | --- |
| `--non-interactive` | 対話モードを無効化（CI 用） |
| `--output-dir PATH` | 出力ディレクトリのパス |
| `--engine-dir PATH` | エンジンキャッシュディレクトリのパス |
| `--settings PATH` | 設定ファイルの出力先（デフォルトパス以外を使いたい場合） |
| `--github-token TOKEN` | GitHub アクセストークン（プライベートリポジトリ用） |
| `--force` | 既存の設定を上書き |

### 設定しない場合のデフォルト値

`config init` を実行しない場合、以下のデフォルト値が使用されます。

| 項目 | デフォルト値 |
| --- | --- |
| **出力ディレクトリ** | `./shogiarena_output`（カレントディレクトリ） |
| **エンジンディレクトリ** | システムの一時ディレクトリ（例: `/tmp/shogiarena-engines`） |

> **Tip: いつ初期化が必要か**
>
> - ✅ **必要な場合**: アーティファクト参照やプレースホルダー（`{output_dir}`, `{engine_dir}`）を使用する
> - ❌ **不要な場合**: 設定ファイルに絶対パスのみを使用する


## 設定の確認と変更

### 現在の設定を表示

```bash
shogiarena config show
```

出力例：
```yaml
output_dir: /home/user/.local/share/shogiarena/output
engine_dir: /home/user/.cache/shogiarena/engines
repos:
  yaneuraou:
    url: https://github.com/yaneurao/YaneuraOu.git
    local_path: /home/user/.cache/shogiarena/repos/yaneuraou
    build_config: /home/user/.config/shogiarena/builds/yaneuraou.yaml
github_token: ghp_xxxxxxxxxxxx
```

### 出力ディレクトリの変更

```bash
shogiarena config set output_dir /new/path/to/output
```

### エンジンディレクトリの変更

```bash
shogiarena config set engine_dir /new/path/to/engines
```

## リポジトリ管理

エンジンのソースコードリポジトリを登録すると、artifact 参照でビルド済みバイナリを使用できます。

### リポジトリの追加

```bash
shogiarena config repo set yaneuraou \
  --url https://github.com/yaneurao/YaneuraOu.git \
  --path ~/repos/YaneuraOu \
  --build-config ~/.config/shogiarena/builds/yaneuraou.yaml
```

| オプション | 説明 |
| --- | --- |
| `--url` | リポジトリの URL（clone に使用） |
| `--path` | ローカルのチェックアウト先 |
| `--build-config` | ビルド設定ファイル（コミット → バイナリのマッピング） |

### リポジトリの削除

```bash
shogiarena config repo remove yaneuraou
```

### リポジトリ一覧の表示

```bash
shogiarena config show
```

## プレースホルダーの使用

設定ファイル内で以下のプレースホルダーを使用できます。実行時に自動的に展開されます。

### {output_dir}

`settings.yaml` で設定した出力ディレクトリに展開されます。

```yaml
# tournament.yaml
experiment_name: "my_experiment"
# 出力先: {output_dir}/tournament/my_experiment-xxxxxxxx/...
```

### {engine_dir}

`settings.yaml` で設定したエンジンキャッシュディレクトリに展開されます。

```yaml
# engine.yaml
path: "{engine_dir}/yaneuraou/YaneuraOu"
options:
  BookFile: "{engine_dir}/books/standard.db"
```

## GitHub トークンの設定

プライベートリポジトリへアクセスする場合、GitHub Personal Access Token (PAT) が必要です。

### トークンの作成

1. GitHub の **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
2. **Generate new token** をクリック
3. 必要な権限: **Contents: Read-only**
4. トークンをコピー

### トークンの設定

```bash
shogiarena config init --github-token ghp_xxxxxxxxxxxx
```

または、`settings.yaml` を直接編集：

```yaml
github_token: ghp_xxxxxxxxxxxx
```

> **Warning: セキュリティ**
>
> トークンは秘密情報です。公開リポジトリやログに含めないように注意してください。


## OpenBench/ShogiBench 連携設定

SPRT 実行時に OpenBench / ShogiBench へ進捗を提出する場合は、`settings.yaml` または run 設定 (`openbench` セクション) で接続先を定義します。

### settings.yaml での設定項目

```yaml
openbench:
  server: https://your-openbench.example.com
  username: your-user
  password_env: OPENBENCH_PASSWORD
```

### パスワードの扱い

- パスワード本体は `settings.yaml` に保存しません。
- `password_env` で指定した環境変数から読み込みます。

```bash
export OPENBENCH_PASSWORD='your-password'
```

`.env` に置く場合も、リポジトリへコミットしない運用を推奨します。

### run 設定で上書きする場合

`tournament.yaml` / `sprt.yaml` に `openbench` セクションを置くと、`settings.yaml` の値より優先されます。

```yaml
openbench:
  enabled: true
  server: https://your-openbench.example.com
  username: your-user
  password_env: OPENBENCH_PASSWORD
  target_test_id: 1234
  submit_interval_games: 2
  strict: true
```

`enabled: true` の場合は `sprt` 設定が必須です。
`mode: existing_test` では `target_test_id` が必須です。`mode: create_test` では `openbench.create` が必須です。

### openbench 設定項目

| キー | 型 | デフォルト | 説明 |
| --- | --- | --- | --- |
| `enabled` | `bool` | `false` | 提出機能を有効化するか |
| `mode` | `str` | `existing_test` | `existing_test` または `create_test` |
| `server` | `str` | `null` | OpenBench/ShogiBench の URL |
| `username` | `str` | `null` | OpenBench のユーザー名 |
| `password_env` | `str` | `OPENBENCH_PASSWORD` | パスワードを読む環境変数名 |
| `target_test_id` | `int` | `null` | 提出先の test ID |
| `submit_interval_games` | `int` | `2` | 何局ごとに送信するか |
| `strict` | `bool` | `true` | 送信失敗時に run を失敗させるか |
| `poll_interval_sec` | `float` | `5.0` | workload 割当ポーリング間隔（秒） |
| `assignment_timeout_sec` | `float` | `120.0` | target test 割当待ちタイムアウト（秒） |
| `allow_insecure_http` | `bool` | `false` | `http://` を許可するか（通常は `false` 推奨） |

### create_test モード（テスト作成を自動化）

`mode: create_test` を使うと、ShogiArena が OpenBench/ShogiBench に `CREATE_TEST` を送信し、作成された test を自動で対象にして提出を開始します。

```yaml
openbench:
  enabled: true
  mode: create_test
  server: https://your-openbench.example.com
  username: your-user
  password_env: OPENBENCH_PASSWORD
  create:
    discovery_timeout_sec: 180
    payload:
      dev_engine: YaneuraOu
      base_engine: YaneuraOu
      dev_repo: https://github.com/yaneurao/YaneuraOu
      base_repo: https://github.com/yaneurao/YaneuraOu
      dev_branch: 3570416
      base_branch: 9f89431a
      dev_options: "Threads=1 Hash=1024"
      base_options: "Threads=1 Hash=1024"
      dev_time_control: "N=100000"
      base_time_control: "N=100000"
```

## 設定例

### 個人開発環境

```bash
shogiarena config init --non-interactive \
  --output-dir ~/shogiarena/output \
  --engine-dir ~/shogiarena/engines
```

`settings.yaml`:
```yaml
output_dir: /home/user/shogiarena/output
engine_dir: /home/user/shogiarena/engines
repos:
  yaneuraou:
    url: https://github.com/yaneurao/YaneuraOu.git
    local_path: /home/user/shogiarena/repos/yaneuraou
```

### CI/CD 環境

```bash
shogiarena config init --non-interactive \
  --output-dir /ci/output \
  --engine-dir /ci/engines \
  --github-token $GITHUB_TOKEN
```

## よくある質問

### 設定を削除したい

```bash
rm ~/.config/shogiarena/settings.yaml
```

次回実行時にデフォルト値が使用されます。

### 複数の設定を使い分けたい

環境変数 `SHOGIARENA_CONFIG` で設定ファイルを切り替えられます。

```bash
export SHOGIARENA_CONFIG=~/my_config/settings.yaml
shogiarena run tournament config.yaml
```

### 設定なしでも動作する？

はい。設定なしでもデフォルトのパス（`./shogiarena_output` など）で動作します。
プレースホルダーやアーティファクト参照を使わない限り、初期化は不要です。

## 参考資料

- [クイックスタート](../getting-started/quick-start.md): 初期設定の基本
- [エンジン設定](engine-configuration.md): プレースホルダーの使用例
- [ビルドシステム](build-system.md): アーティファクト参照の詳細
